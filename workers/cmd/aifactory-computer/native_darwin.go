//go:build darwin

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

func platformToken() string           { return "/usr/local/lib/aifactory-computer/agent.token" }
func desktopSession() error           { return nil }
func platformCall(r request) response { return native(r) }
func native(r request) response {
	exe, _ := os.Executable()
	helper := filepath.Join(filepath.Dir(exe), "desktop-native")
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, helper)
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
