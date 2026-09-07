//go:build !windows

package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestLifecycleOwnsOnlyFreshCloneAndReleasesIt(t *testing.T) {
	j, _ := newJournal(t.TempDir())
	defer j.lock.Close()
	w := &worker{c: config{BaseVM: "base", GuestVM: "guest", Tart: "/approved/tart"}, j: j}
	exists, running := false, false
	var calls [][]string
	w.command = func(ctx context.Context, path string, args ...string) *exec.Cmd {
		if path != "/approved/tart" {
			t.Fatal(path)
		}
		calls = append(calls, append([]string{}, args...))
		output := ""
		switch args[0] {
		case "list":
			output = "[]"
			if exists {
				state := "stopped"
				if running {
					state = "running"
				}
				b, _ := json.Marshal([]map[string]string{{"Source": "local", "Name": "guest", "State": state}})
				output = string(b)
			}
		case "clone":
			if args[1] != "base" || args[2] != "guest" {
				t.Fatal(args)
			}
			exists = true
		case "run":
			joined := strings.Join(args, " ")
			for _, required := range []string{"--no-clipboard", "--no-audio", "--net-softnet-block="} {
				if !strings.Contains(joined, required) {
					t.Fatal(args)
				}
			}
			for _, forbidden := range []string{"--dir", "--net-softnet-allow", "--net-bridged"} {
				if strings.Contains(joined, forbidden) {
					t.Fatal(args)
				}
			}
			running = true
		case "stop":
			running = false
		case "delete":
			if running || args[1] != "guest" {
				t.Fatal(args)
			}
			exists = false
		}
		cmd := exec.CommandContext(ctx, "/bin/sh", "-c", "printf '%s' \"$FAKE_OUTPUT\"")
		cmd.Env = append(os.Environ(), "FAKE_OUTPUT="+output)
		return cmd
	}
	j.begin("prepare")
	op := operation{ID: "prepare", Kind: "guest-prepare"}
	op.Payload.Lease = "lease-1"
	r := w.lifecycle(context.Background(), op, &logWriter{j: j, id: op.ID})
	if r.Status != "succeeded" || !w.ownsLease("lease-1") || !exists {
		t.Fatal(r, calls)
	}
	j.begin("wrong")
	op.ID = "wrong"
	op.Kind = "guest-release"
	op.Payload.Lease = "lease-2"
	before := len(calls)
	if r = w.lifecycle(context.Background(), op, &logWriter{j: j, id: op.ID}); r.Status != "uncertain" || len(calls) != before {
		t.Fatal("wrong lease touched VM")
	}
	j.begin("release")
	op.ID = "release"
	op.Payload.Lease = "lease-1"
	r = w.lifecycle(context.Background(), op, &logWriter{j: j, id: op.ID})
	if r.Status != "succeeded" || exists || w.ownsLease("lease-1") {
		t.Fatal(r, calls)
	}
}

func TestPrepareRefusesPreexistingGuest(t *testing.T) {
	j, _ := newJournal(t.TempDir())
	defer j.lock.Close()
	j.begin("prepare")
	w := &worker{c: config{BaseVM: "base", GuestVM: "guest", Tart: "/approved/tart"}, j: j}
	w.command = func(ctx context.Context, path string, args ...string) *exec.Cmd {
		if args[0] != "list" {
			t.Fatal("preexisting VM mutated", args)
		}
		return exec.CommandContext(ctx, "/bin/sh", "-c", `printf '%s' '[{"Source":"local","Name":"guest","State":"stopped"}]'`)
	}
	op := operation{ID: "prepare", Kind: "guest-prepare"}
	op.Payload.Lease = "lease-1"
	if r := w.lifecycle(context.Background(), op, &logWriter{j: j, id: op.ID}); r.Status != "uncertain" {
		t.Fatal(r)
	}
	if _, err := os.Stat(w.leasePath()); !os.IsNotExist(err) {
		t.Fatal("preexisting guest claimed")
	}
}

func TestInputUsesStdinNotHostCommandArguments(t *testing.T) {
	j, _ := newJournal(t.TempDir())
	defer j.lock.Close()
	j.begin("input")
	w := &worker{c: config{GuestVM: "guest", Tart: "/approved/tart"}, j: j, done: make(chan struct{})}
	w.lastOK.Store(time.Now().UnixNano())
	w.command = func(ctx context.Context, path string, args ...string) *exec.Cmd {
		if strings.Contains(strings.Join(args, " "), "private-value") || strings.Join(args[:3], " ") != "exec -i guest" {
			t.Fatal(args)
		}
		return exec.CommandContext(ctx, "/bin/sh", "-c", `read value; test "$value" = "private-value"`)
	}
	op := operation{ID: "input", Kind: "guest-exec", Input: "private-value\n"}
	op.Payload.Command = "read value"
	op.Payload.Timeout = 2
	w.execute(context.Background(), op)
	b, _ := os.ReadFile(filepath.Join(j.dir(op.ID), "result.json"))
	var r result
	json.Unmarshal(b, &r)
	if r.Status != "succeeded" || j.count(op.ID) != 0 {
		t.Fatal(string(b))
	}
}

func TestGuestCancellationConfirmsVMStopped(t *testing.T) {
	j, _ := newJournal(t.TempDir())
	defer j.lock.Close()
	j.begin("cancel-op")
	w := &worker{c: config{GuestVM: "guest", Tart: "/approved/tart"}, j: j, done: make(chan struct{})}
	w.lastOK.Store(time.Now().UnixNano())
	stopped := false
	w.command = func(ctx context.Context, path string, args ...string) *exec.Cmd {
		switch args[0] {
		case "exec":
			return exec.CommandContext(ctx, "/bin/sh", "-c", "sleep 30")
		case "stop":
			stopped = true
			return exec.CommandContext(ctx, "/usr/bin/true")
		case "list":
			if !stopped {
				t.Fatal("stop not requested")
			}
			return exec.CommandContext(ctx, "/bin/sh", "-c", `printf '%s' '[{"Source":"local","Name":"guest","State":"stopped"}]'`)
		}
		t.Fatal(args)
		return nil
	}
	op := operation{ID: "cancel-op", Kind: "guest-exec"}
	op.Payload.Command = "sleep 30"
	op.Payload.Timeout = 10
	ctx, cancel := context.WithCancel(context.Background())
	time.AfterFunc(100*time.Millisecond, cancel)
	w.execute(ctx, op)
	b, _ := os.ReadFile(filepath.Join(j.dir(op.ID), "result.json"))
	var r result
	json.Unmarshal(b, &r)
	if !stopped || r.Status != "cancelled" {
		t.Fatal(string(b))
	}
}
