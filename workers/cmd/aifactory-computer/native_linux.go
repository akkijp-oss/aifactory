//go:build linux

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

func platformToken() string { return "/var/lib/aifactory-worker/desktop/agent.token" }
func desktopSession() error {
	if os.Getenv("DISPLAY") == "" || (os.Getenv("WAYLAND_DISPLAY") != "" || os.Getenv("XDG_SESSION_TYPE") == "wayland") {
		return errors.New("an X11 desktop is required; native Wayland is not supported")
	}
	return nil
}
func platformCall(r request) response { return remote(r) }
func native(r request) response {
	exe, _ := os.Executable()
	helper := filepath.Join(filepath.Dir(exe), "linux.py")
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "/usr/bin/python3", helper)
	b, _ := json.Marshal(r)
	cmd.Stdin = bytes.NewReader(b)
	var output bytes.Buffer
	cmd.Stdout = &output
	cmd.WaitDelay = time.Second
	e := cmd.Run()
	var out response
	if json.Unmarshal(output.Bytes(), &out) != nil {
		return response{Error: "desktop helper unavailable or permission denied"}
	}
	if e != nil && out.OK {
		return response{Error: "desktop operation interrupted"}
	}
	return out
}
