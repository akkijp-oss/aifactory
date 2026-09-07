//go:build linux

package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestLinuxConfigurationRejectsHypervisorAndPrivateOverlap(t *testing.T) {
	valid := config{WorkRoot: "/var/lib/aifactory-worker/work", TaskUser: "aifactory-task", StateDir: "/var/lib/aifactory-worker/private/state", TokenFile: "/etc/aifactory/token"}
	if e := validateLinuxConfig(&valid); e != nil {
		t.Fatal(e)
	}
	for _, change := range []func(*config){func(c *config) { c.Tart = "/bin/tart" }, func(c *config) { c.WorkRoot = "/" }, func(c *config) { c.TaskUser = "../root" }, func(c *config) { c.TokenFile = c.WorkRoot + "/token" }} {
		c := valid
		change(&c)
		if validateLinuxConfig(&c) == nil {
			t.Fatal("unsafe configuration accepted", c)
		}
	}
}
func TestLinuxSystemdWorkspaceAndProcessCleanup(t *testing.T) {
	if os.Getenv("AIFACTORY_LINUX_SYSTEMD_TEST") != "1" || os.Geteuid() != 0 {
		t.Skip("requires explicit root systemd integration environment")
	}
	root := t.TempDir()
	os.Chmod(filepath.Dir(root), 0755)
	os.Chmod(root, 0755)
	state := filepath.Join(root, "state")
	work := filepath.Join(root, "work")
	os.Mkdir(work, 0755)
	j, e := newJournal(state)
	if e != nil {
		t.Fatal(e)
	}
	defer j.lock.Close()
	w := &worker{c: config{WorkRoot: work, StateDir: state, TaskUser: "nobody"}, j: j}
	lease := "lease-test"
	n := 0
	execute := func(kind, command string, timeout int) result {
		n++
		op := operation{ID: fmt.Sprintf("linux-test-%d-%d", os.Getpid(), n), Kind: kind}
		op.Payload.Lease = lease
		op.Payload.Command = command
		op.Payload.Timeout = timeout
		if _, e := j.begin(op.ID); e != nil {
			t.Fatal(e)
		}
		lw := &logWriter{j: j, id: op.ID}
		r := w.executeLinux(context.Background(), op, lw)
		lw.flush()
		if r.Status == "uncertain" {
			for i := 0; i < j.count(op.ID); i++ {
				b, _ := os.ReadFile(j.event(op.ID, i))
				t.Log(string(b))
			}
		}
		return r
	}
	if r := execute("guest-prepare", "", 0); r.Status != "succeeded" {
		t.Fatal(r)
	}
	if r := execute("guest-prepare", "", 0); r.Status != "uncertain" {
		t.Fatal("reprepared owned workspace", r)
	}
	if r := execute("guest-exec", "value=literal; test \"$value\" = literal && test $(id -u) -ne 0 && printf '日本語' > proof.txt", 10); r.Status != "succeeded" {
		t.Fatal(r)
	}
	if b, _ := os.ReadFile(filepath.Join(work, lease, "proof.txt")); string(b) != "日本語" {
		t.Fatal("unicode mismatch")
	}
	if r := execute("guest-exec", "exit 7", 10); r.ExitCode == nil || *r.ExitCode != 7 {
		t.Fatal("lost nonzero status", r)
	}
	for _, command := range []string{"sleep 600 & echo $! > child.pid", "sleep 600 & echo $! > child.pid; wait"} {
		r := execute("guest-exec", command, 1)
		if r.Status == "uncertain" {
			t.Fatal("cleanup uncertain", r)
		}
		b, e := os.ReadFile(filepath.Join(work, lease, "child.pid"))
		if e != nil {
			t.Fatal(e)
		}
		pid, _ := strconv.Atoi(strings.TrimSpace(string(b)))
		deadline := time.Now().Add(3 * time.Second)
		for syscall.Kill(pid, 0) == nil && time.Now().Before(deadline) {
			time.Sleep(30 * time.Millisecond)
		}
		if syscall.Kill(pid, 0) == nil {
			t.Fatal("descendant survived", pid)
		}
	}
	outside := filepath.Join(root, "outside")
	os.Mkdir(outside, 0755)
	os.WriteFile(filepath.Join(outside, "keep"), []byte("safe"), 0600)
	if r := execute("guest-exec", "ln -s '"+outside+"' outside-link", 10); r.Status != "succeeded" {
		t.Fatal(r)
	}
	if r := execute("guest-release", "", 0); r.Status != "succeeded" {
		t.Fatal(r)
	}
	if _, e := os.Stat(filepath.Join(outside, "keep")); e != nil {
		t.Fatal("cleanup followed symlink", e)
	}
	if w.ownsLease(lease) {
		t.Fatal("lease retained after successful cleanup")
	}
}
