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
	"runtime"
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
}

// A screenshot tool_result carries about a megabyte of base64 per line. The log keeps the
// line as valid JSON but replaces the payload with its decoded size; the image itself stays
// in the guest as a file.
func TestLogReplacesImageBase64(t *testing.T) {
	j, _ := newJournal(t.TempDir())
	defer j.lock.Close()
	j.begin("op")
	w := &logWriter{j: j, id: "op"}
	data := strings.Repeat("QUJD", 300000) // 1,200,000 base64 chars => 900,000 bytes
	line := `{"type":"user","message":{"content":[{"type":"tool_result","content":[` +
		`{"type":"image","source":{"type":"base64","media_type":"image/png","data":"` + data + `"}},` +
		`{"type":"text","text":"screenshot taken"}]}]}}` + "\n"
	b := []byte(line)
	for i := 0; i < len(b); i += 7 {
		if n, err := w.Write(b[i:min(i+7, len(b))]); err != nil || n != len(b[i:min(i+7, len(b))]) {
			t.Fatal(n, err)
		}
	}
	if err := w.flush(); err != nil {
		t.Fatal(err)
	}
	var out strings.Builder
	for n := 0; n < w.seq; n++ {
		raw, _ := os.ReadFile(j.event("op", n))
		var s string
		json.Unmarshal(raw, &s)
		out.WriteString(s)
	}
	got := out.String()
	if strings.Contains(got, data[:4096]) {
		t.Fatal("image base64 kept in the operation log")
	}
	if !strings.Contains(got, `"data":"[image 900000 bytes]"`) {
		t.Fatalf("no size marker: %.400s", got)
	}
	if !strings.Contains(got, "screenshot taken") {
		t.Fatal("surrounding stream-json text lost")
	}
	if len(got) > 4096 || w.size != len(got) {
		t.Fatal(len(got), w.size)
	}
	// The runner parses each stream-json line, so the replacement must stay valid JSON.
	var event any
	if err := json.Unmarshal([]byte(strings.TrimSuffix(got, "\n")), &event); err != nil {
		t.Fatal(err)
	}
}

// Reaching the limit truncates the log; it is not an error and must not fail the operation.
func TestLogLimitTruncatesWithoutError(t *testing.T) {
	j, _ := newJournal(t.TempDir())
	defer j.lock.Close()
	j.begin("op")
	w := &logWriter{j: j, id: "op"}
	w.size = maxLog - 100
	b := []byte(strings.Repeat("x", 1024) + "\n")
	if n, err := w.Write(b); err != nil || n != len(b) {
		t.Fatal(n, err)
	}
	if !w.truncated {
		t.Fatal("truncation not recorded")
	}
	if w.seq != 1 {
		t.Fatal("expected exactly the truncation marker", w.seq)
	}
	raw, _ := os.ReadFile(j.event("op", 0))
	var marker string
	json.Unmarshal(raw, &marker)
	if !strings.Contains(marker, "truncated") {
		t.Fatal(marker)
	}
	if w.size > maxLog {
		t.Fatal("log exceeded the quota", w.size)
	}
	// Later output is discarded without a second marker and without an error.
	if n, err := w.Write([]byte("more\n")); err != nil || n != 5 {
		t.Fatal(n, err)
	}
	if err := w.flush(); err != nil {
		t.Fatal(err)
	}
	if w.seq != 1 {
		t.Fatal("wrote past the truncation marker", w.seq)
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
	// The first tick polls op1, marks it fresh in the journal and starts execute in the background.
	if err := w.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	// Wait for execute to finish (it closes w.done) instead of polling on wall-clock time: once
	// done is closed the event and result.json are on disk, however slow the filesystem is.
	// White box: w.done is created by tick when the operation is fresh and closed by execute.
	select {
	case <-w.done:
	case <-time.After(30 * time.Second):
		t.Fatal("probe did not finish")
	}
	// Deterministic from here, no sleeping: tick 2 ships the event and gets 503 from complete,
	// tick 3 completes. Stop at the first accepted completion; a further tick would poll op1
	// again and re-send complete.
	for i := 0; i < 5; i++ {
		w.tick(context.Background())
		mu.Lock()
		done := accepted
		mu.Unlock()
		if done {
			break
		}
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
	if runtime.GOOS == "windows" {
		t.Skip("Tart backend")
	}
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

// #280: a guest command whose output exceeds the log limit used to break the tart exec pipe
// and report uncertain while the guest kept running. The exit code now decides the result.
func TestGuestExecutionSurvivesLogLimit(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Tart backend")
	}
	for _, tc := range []struct {
		name   string
		script string
		status string
		code   int
	}{
		{"success", "yes | head -c 20000000; exit 0", "succeeded", 0},
		{"failure", "yes | head -c 20000000; exit 3", "failed", 3},
	} {
		t.Run(tc.name, func(t *testing.T) {
			j, _ := newJournal(t.TempDir())
			defer j.lock.Close()
			j.begin("guest-op")
			w := &worker{c: config{Tart: "/approved/tart", GuestVM: "isolated"}, j: j, done: make(chan struct{})}
			w.lastOK.Store(time.Now().UnixNano())
			w.command = func(ctx context.Context, _ string, _ ...string) *exec.Cmd {
				return exec.CommandContext(ctx, "/bin/sh", "-c", tc.script)
			}
			op := operation{ID: "guest-op", Kind: "guest-exec"}
			op.Payload.Command = tc.script
			op.Payload.Timeout = 120
			w.execute(context.Background(), op)
			b, _ := os.ReadFile(filepath.Join(j.dir(op.ID), "result.json"))
			var r result
			json.Unmarshal(b, &r)
			if r.Status != tc.status || r.ExitCode == nil || *r.ExitCode != tc.code {
				t.Fatal(string(b))
			}
			if !r.Truncated {
				t.Fatal("truncation not reported in the result", string(b))
			}
			total := 0
			for n := 0; n < r.Events; n++ {
				raw, _ := os.ReadFile(j.event(op.ID, n))
				var s string
				json.Unmarshal(raw, &s)
				total += len(s)
			}
			if total > maxLog {
				t.Fatal("log exceeded the quota", total)
			}
		})
	}
}
