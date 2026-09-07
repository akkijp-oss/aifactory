package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func (w *worker) leasePath() string { return filepath.Join(w.j.root, "guest-lease") }
func (w *worker) ownsLease(id string) bool {
	b, err := os.ReadFile(w.leasePath())
	return err == nil && nameRE.MatchString(id) && string(b) == id
}

func (w *worker) guestExists(ctx context.Context) (bool, bool, error) {
	return w.vmExists(ctx, w.c.GuestVM)
}

func (w *worker) vmExists(ctx context.Context, name string) (bool, bool, error) {
	b, err := w.command(ctx, w.c.Tart, "list", "--format", "json").Output()
	if err != nil {
		return false, false, err
	}
	var rows []struct {
		Name   string `json:"Name"`
		State  string `json:"State"`
		Source string `json:"Source"`
	}
	if err = json.Unmarshal(b, &rows); err != nil {
		return false, false, err
	}
	for _, row := range rows {
		if row.Name == name && row.Source == "local" {
			return true, !strings.EqualFold(row.State, "stopped"), nil
		}
	}
	return false, false, nil
}

func (w *worker) lifecycle(ctx context.Context, op operation, lw *logWriter) (r result) {
	uncertain := result{Status: "uncertain"}
	defer func() {
		if r.Status != "succeeded" && w.ownsLease(op.Payload.Lease) {
			w.stopGuest()
		}
	}()
	if w.c.BaseVM == "" || !nameRE.MatchString(op.Payload.Lease) {
		return uncertain
	}
	ctx, cancel := context.WithTimeout(ctx, 600*time.Second)
	defer cancel()
	run := func(args ...string) error {
		cmd := w.command(ctx, w.c.Tart, args...)
		cmd.Stdout, cmd.Stderr = lw, lw
		return cmd.Run()
	}
	if op.Kind == "guest-prepare" {
		// Refuse even an idle pre-existing VM: only this worker's new clone is owned.
		if _, err := os.Stat(w.leasePath()); !os.IsNotExist(err) {
			return uncertain
		}
		exists, _, err := w.guestExists(ctx)
		if err != nil || exists {
			return uncertain
		}
		if err = writeAtomic(w.leasePath(), []byte(op.Payload.Lease)); err != nil {
			return uncertain
		}
		if err = run("clone", w.c.BaseVM, w.c.GuestVM); err != nil {
			return uncertain
		}
		if err = run("set", w.c.GuestVM, "--cpu", "4", "--memory", "8192"); err != nil {
			return uncertain
		}
		logFile, err := os.OpenFile(filepath.Join(w.j.root, "guest-console.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
		if err != nil {
			return uncertain
		}
		// No host directories, clipboard, audio, incoming ports, or private-network exception.
		cmd := w.command(context.Background(), w.c.Tart, "run", "--no-graphics", "--no-audio", "--no-clipboard",
			"--net-softnet", "--net-softnet-block=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,169.254.0.0/16", w.c.GuestVM)
		cmd.Stdout, cmd.Stderr = logFile, logFile
		if err = cmd.Start(); err != nil {
			logFile.Close()
			return uncertain
		}
		go func() { cmd.Wait(); logFile.Close() }()
		ready := false
		for i := 0; i < 90; i++ {
			if ctx.Err() != nil {
				w.stopGuest()
				return uncertain
			}
			probeCtx, done := context.WithTimeout(ctx, 5*time.Second)
			err = w.command(probeCtx, w.c.Tart, "exec", w.c.GuestVM, "/usr/bin/true").Run()
			done()
			if err == nil {
				ready = true
				break
			}
			select {
			case <-ctx.Done():
				w.stopGuest()
				return uncertain
			case <-time.After(2 * time.Second):
			}
		}
		if !ready {
			w.stopGuest()
			return uncertain
		}
		if err = run("exec", w.c.GuestVM, "/bin/bash", "-lc", "sudo networksetup -setdnsservers Ethernet 1.1.1.1 8.8.8.8 && sudo networksetup -setv6off Ethernet"); err != nil {
			return uncertain
		}
		fmt.Fprintln(lw, "guest prepared; lease="+op.Payload.Lease)
	} else {
		if !w.ownsLease(op.Payload.Lease) {
			return uncertain
		}
		exists, running, err := w.guestExists(ctx)
		if err != nil {
			return uncertain
		}
		if running && !w.stopGuest() {
			return uncertain
		}
		exists, running, err = w.guestExists(ctx)
		if err != nil || running {
			return uncertain
		}
		if exists && run("delete", w.c.GuestVM) != nil {
			return uncertain
		}
		exists, _, err = w.guestExists(ctx)
		if err != nil || exists {
			return uncertain
		}
		if err = os.Remove(w.leasePath()); err != nil {
			return uncertain
		}
		fmt.Fprintln(lw, "guest deleted; lease="+op.Payload.Lease)
	}
	zero := 0
	return result{Status: "succeeded", ExitCode: &zero}
}
