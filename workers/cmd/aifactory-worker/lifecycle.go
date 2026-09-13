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
	switch op.Kind {
	case "guest-prepare":
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
		// Resolution comes from the PJ definition first, then this worker's config; without
		// either, the base image keeps its own size. Tart only accepts it while stopped, so
		// this stays before run, and separate from the cpu/memory call that must not change.
		width, height := op.Payload.Width, op.Payload.Height
		if width == 0 && height == 0 && w.c.Display != nil {
			width, height = w.c.Display.Width, w.c.Display.Height
		}
		if width != 0 || height != 0 {
			if !validDisplay(width, height) {
				return uncertain
			}
			if err = run("set", w.c.GuestVM, "--display", fmt.Sprintf("%dx%d", width, height)); err != nil {
				return uncertain
			}
		}
		if err = w.bootGuest(ctx, lw); err != nil {
			return uncertain
		}
		fmt.Fprintln(lw, "guest prepared; lease="+op.Payload.Lease)
	case "guest-start":
		// 止まっているだけのゲストを起動し直すだけの操作（チケット 478）。人が検査のために残した
		// ゲストと lease を守るため、clone / delete / lease ファイルの書き換えはどれもしない。
		if !w.ownsLease(op.Payload.Lease) {
			return uncertain
		}
		exists, running, err := w.guestExists(ctx)
		if err != nil {
			return uncertain
		}
		if !exists {
			// ゲストが無い＝起動し直せない。tart を何も呼んでいないことがここで確定しているので、
			// worker を塞ぐ uncertain ではなく failed で返す（黙って作り直さない）。
			fmt.Fprintln(lw, "guest missing; nothing changed; lease="+op.Payload.Lease)
			one := 1
			return result{Status: "failed", ExitCode: &one}
		}
		// 既に動いているゲスト（人が手で起動した回）には run を重ねず、待ち受けだけして冪等に返す。
		if running {
			err = w.awaitGuest(ctx, lw)
		} else {
			err = w.bootGuest(ctx, lw)
		}
		if err != nil {
			return uncertain
		}
		fmt.Fprintln(lw, "guest started; lease="+op.Payload.Lease)
	default:
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

// bootGuest は既にあるゲストを起動し、中に入れるようになるまで待つ。guest-prepare（clone の直後）と
// guest-start（止まっていたゲストの起動し直し）で同じ手順・同じフラグを使うための共通化（チケット 478）。
func (w *worker) bootGuest(ctx context.Context, lw *logWriter) error {
	logFile, err := os.OpenFile(filepath.Join(w.j.root, "guest-console.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	// No host directories, clipboard, audio, incoming ports, or private-network exception.
	cmd := w.command(context.Background(), w.c.Tart, "run", "--no-graphics", "--no-audio", "--no-clipboard",
		"--net-softnet", "--net-softnet-block=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,169.254.0.0/16", w.c.GuestVM)
	cmd.Stdout, cmd.Stderr = logFile, logFile
	if err = cmd.Start(); err != nil {
		logFile.Close()
		return err
	}
	go func() { cmd.Wait(); logFile.Close() }()
	return w.awaitGuest(ctx, lw)
}

// awaitGuest は tart exec が通るまで待ってから DNS/IPv6 を設定する。既に動いているゲストにも使えるので、
// guest-start は「人が手で起動済み」の回にも tart run を重ねずに済む。
func (w *worker) awaitGuest(ctx context.Context, lw *logWriter) error {
	ready := false
	for i := 0; i < 90; i++ {
		if ctx.Err() != nil {
			w.stopGuest()
			return ctx.Err()
		}
		probeCtx, done := context.WithTimeout(ctx, 5*time.Second)
		err := w.command(probeCtx, w.c.Tart, "exec", w.c.GuestVM, "/usr/bin/true").Run()
		done()
		if err == nil {
			ready = true
			break
		}
		select {
		case <-ctx.Done():
			w.stopGuest()
			return ctx.Err()
		case <-time.After(2 * time.Second):
		}
	}
	if !ready {
		w.stopGuest()
		return fmt.Errorf("guest did not become reachable")
	}
	cmd := w.command(ctx, w.c.Tart, "exec", w.c.GuestVM, "/bin/bash", "-lc",
		"sudo networksetup -setdnsservers Ethernet 1.1.1.1 8.8.8.8 && sudo networksetup -setv6off Ethernet")
	cmd.Stdout, cmd.Stderr = lw, lw
	return cmd.Run()
}
