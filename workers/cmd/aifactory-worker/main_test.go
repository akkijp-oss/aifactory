package main

import (
	"context"
	"encoding/json"
	"encoding/pem"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestJournalSingleOwnerAndCrashRecovery(t *testing.T) {
	root := t.TempDir()
	j, err := newJournal(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = newJournal(root); err == nil {
		t.Fatal("second owner accepted")
	}
	if fresh, err := j.begin("op-1"); err != nil || !fresh {
		t.Fatal(fresh, err)
	}
	j.lock.Close()
	j, err = newJournal(root)
	if err != nil {
		t.Fatal(err)
	}
	defer j.lock.Close()
	if fresh, err := j.begin("op-1"); err != nil || fresh {
		t.Fatal("restarted operation would execute", err)
	}
	if _, err := j.begin("../escape"); err == nil {
		t.Fatal("path traversal accepted")
	}
}

func TestLogChunksPreserveUnicodeAndBoundSize(t *testing.T) {
	j, _ := newJournal(t.TempDir())
	defer j.lock.Close()
	j.begin("op")
	w := &logWriter{j: j, id: "op"}
	text := strings.Repeat("日", 7000) + "end"
	b := []byte(text)
	for i := 0; i < len(b); i += 7 {
		if _, err := w.Write(b[i:min(i+7, len(b))]); err != nil {
			t.Fatal(err)
		}
	}
	if err := w.flush(); err != nil {
		t.Fatal(err)
	}
	var out strings.Builder
	for n := 0; n < w.seq; n++ {
		b, _ := os.ReadFile(j.event("op", n))
		var s string
		json.Unmarshal(b, &s)
		out.WriteString(s)
	}
	if out.String() != text {
		t.Fatal("unicode corrupted")
	}
	w.size = maxLog
	if _, err := w.Write([]byte("too much")); err == nil {
		t.Fatal("log quota ignored")
	}
}

func setupWorker(t *testing.T, handler http.Handler) *worker {
	t.Helper()
	srv := httptest.NewTLSServer(handler)
	t.Cleanup(srv.Close)
	dir := t.TempDir()
	token := filepath.Join(dir, "token")
	ca := filepath.Join(dir, "ca.pem")
	os.WriteFile(token, []byte(strings.Repeat("t", 40)), 0600)
	os.WriteFile(ca, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: srv.Certificate().Raw}), 0600)
	w, err := newWorker(config{Worker: "mac1", URL: srv.URL, TokenFile: token, CAFile: ca, StateDir: filepath.Join(dir, "state")})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { w.j.lock.Close() })
	return w
}

func TestPullProbeRetriesWithoutDuplicateExecution(t *testing.T) {
	var mu sync.Mutex
	events := map[int]string{}
	attempts := 0
	accepted := false
	w := setupWorker(t, http.HandlerFunc(func(rw http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		if r.Header.Get("Authorization") != "Bearer "+strings.Repeat("t", 40) || r.Header.Get("X-Worker-ID") != "mac1" {
			t.Error("auth headers missing")
		}
		var data map[string]json.RawMessage
		json.NewDecoder(r.Body).Decode(&data)
		if string(data["version"]) != "1" {
			t.Error("missing protocol version")
		}
		rw.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/v1/heartbeat":
			io.WriteString(rw, `{"cancel":[]}`)
		case "/v1/poll":
			io.WriteString(rw, `{"operation":{"id":"op1","kind":"probe","payload":{},"cancelled":0}}`)
		case "/v1/event":
			var seq int
			var text string
			json.Unmarshal(data["seq"], &seq)
			json.Unmarshal(data["text"], &text)
			if old, ok := events[seq]; ok && old != text {
				t.Error("changed replay")
			}
			events[seq] = text
			json.NewEncoder(rw).Encode(map[string]int{"ack": seq})
		case "/v1/complete":
			attempts++
			if attempts == 1 {
				rw.WriteHeader(503)
				return
			}
			var res result
			json.Unmarshal(data["result"], &res)
			if res.Status != "succeeded" || res.Events != len(events) {
				t.Error(res)
			}
			accepted = true
			io.WriteString(rw, `{"accepted":true}`)
		default:
			t.Error(r.URL.Path)
		}
	}))
	for i := 0; i < 50; i++ {
		w.tick(context.Background())
		mu.Lock()
		done := accepted
		mu.Unlock()
		if done {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	mu.Lock()
	defer mu.Unlock()
	if !accepted || attempts != 2 || len(events) != 1 {
		t.Fatal(accepted, attempts, len(events))
	}
}

func TestRestartReportsUncertainWithoutRunningCommand(t *testing.T) {
	reported := ""
	w := setupWorker(t, http.HandlerFunc(func(rw http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/heartbeat":
			io.WriteString(rw, `{"cancel":[]}`)
		case "/v1/poll":
			io.WriteString(rw, `{"operation":{"id":"crashed","kind":"guest-exec","payload":{"command":"danger","timeout":1}}}`)
		case "/v1/complete":
			var d struct {
				Result result `json:"result"`
			}
			json.NewDecoder(r.Body).Decode(&d)
			reported = d.Result.Status
			io.WriteString(rw, `{"accepted":true}`)
		}
	}))
	w.j.begin("crashed")
	w.command = func(context.Context, string, ...string) *exec.Cmd { t.Fatal("re-executed crashed command"); return nil }
	if err := w.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if reported != "uncertain" {
		t.Fatal(reported)
	}
}

func TestGuestExecutionUsesOnlyConfiguredVM(t *testing.T) {
	j, _ := newJournal(t.TempDir())
	defer j.lock.Close()
	j.begin("guest-op")
	w := &worker{c: config{Tart: "/approved/tart", GuestVM: "isolated"}, j: j, done: make(chan struct{})}
	w.lastOK.Store(time.Now().UnixNano())
	w.command = func(ctx context.Context, path string, args ...string) *exec.Cmd {
		if path != "/approved/tart" || strings.Join(args[:5], "|") != "exec|isolated|/bin/bash|-lc|echo guest" {
			t.Fatalf("unexpected host command: %s %q", path, args)
		}
		return exec.CommandContext(ctx, "/bin/sh", "-c", "echo guest")
	}
	op := operation{ID: "guest-op", Kind: "guest-exec"}
	op.Payload.Command = "echo guest"
	op.Payload.Timeout = 2
	w.execute(context.Background(), op)
	b, _ := os.ReadFile(filepath.Join(j.dir(op.ID), "result.json"))
	var r result
	json.Unmarshal(b, &r)
	if r.Status != "succeeded" || r.ExitCode == nil || *r.ExitCode != 0 {
		t.Fatal(string(b))
	}
}

func TestTLSRefusesUnknownCertificateAndRedirect(t *testing.T) {
	w := setupWorker(t, http.HandlerFunc(func(rw http.ResponseWriter, r *http.Request) {
		http.Redirect(rw, r, "https://example.invalid", http.StatusFound)
	}))
	var out any
	if err := w.post(context.Background(), "poll", map[string]any{}, &out); err == nil {
		t.Fatal("redirect followed")
	}
	// A different trust root must not be accepted even though the server is reachable.
	caFile := w.c.CAFile
	os.WriteFile(caFile, []byte("invalid"), 0600)
	if _, err := newWorker(w.c); err == nil {
		t.Fatal("invalid trust root accepted")
	}
}
