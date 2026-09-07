//go:build windows

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"golang.org/x/sys/windows"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"
)

func platformToken() string           { return `C:\ProgramData\AIFactoryWorker\desktop\agent.token` }
func platformCall(r request) response { return remote(r) }
func desktopSession() error {
	var id uint32
	if e := windows.ProcessIdToSessionId(uint32(os.Getpid()), &id); e != nil {
		return e
	}
	if id == 0 {
		return errors.New("desktop agent must run in a logged-in user session")
	}
	return nil
}
func native(r request) response {
	exe, _ := os.Executable()
	script := filepath.Join(filepath.Dir(exe), "windows.ps1")
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, filepath.Join(os.Getenv("SystemRoot"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script)
	b, _ := json.Marshal(r)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	cmd.Stdin = bytes.NewReader(b)
	var output bytes.Buffer
	cmd.Stdout = &output
	cmd.WaitDelay = time.Second
	e := cmd.Run()
	var out response
	if json.Unmarshal(output.Bytes(), &out) != nil {
		return response{Error: "desktop operation failed"}
	}
	if e != nil && out.OK {
		return response{Error: "desktop operation interrupted"}
	}
	return out
}
