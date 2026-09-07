//go:build windows

package main

import (
	"bytes"
	"context"
	"fmt"
	"golang.org/x/sys/windows"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestMain(m *testing.M) {
	if len(os.Args) > 1 && os.Args[1] == "--job-child" {
		jobChild()
		return
	}
	os.Exit(m.Run())
}
func windowsTestJob(t *testing.T, script string, timeout time.Duration) (int, string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	var out bytes.Buffer
	request := childRequest{Program: filepath.Join(os.Getenv("SystemRoot"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), Args: []string{"-NoProfile", "-NonInteractive", "-EncodedCommand", encodePowerShell("$ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); " + script)}, Directory: t.TempDir()}
	code, err := runWindowsJob(ctx, 0, request, &out)
	if err != nil {
		t.Fatal(err, out.String())
	}
	return code, out.String()
}
func TestWindowsUnicodeAndExitCode(t *testing.T) {
	code, out := windowsTestJob(t, "[Console]::WriteLine('日本語の成果物'); exit 7", 20*time.Second)
	if code != 7 || !strings.Contains(out, "日本語の成果物") {
		t.Fatal(code, out)
	}
}
func TestWindowsJobKillsBackgroundDescendant(t *testing.T) {
	for _, wait := range []bool{false, true} {
		t.Run(fmt.Sprint(wait), func(t *testing.T) {
			script := `$p=Start-Process powershell.exe -ArgumentList '-NoProfile -Command Start-Sleep 120' -PassThru -NoNewWindow; [Console]::WriteLine($p.Id); `
			if wait {
				script += "Start-Sleep 120"
			}
			started := time.Now()
			_, out := windowsTestJob(t, script, 5*time.Second)
			if time.Since(started) > 12*time.Second {
				t.Fatal("job cleanup exceeded bound")
			}
			pid, err := strconv.Atoi(strings.TrimSpace(out))
			if err != nil {
				t.Fatal(out, err)
			}
			handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
			if err == nil {
				defer windows.CloseHandle(handle)
				var code uint32
				if err = windows.GetExitCodeProcess(handle, &code); err != nil || code == 259 {
					t.Fatal("descendant remains alive", code, err)
				}
			}
		})
	}
}
func TestWindowsConfigRejectsTaskSecretInWorkRoot(t *testing.T) {
	c := config{WorkRoot: `C:\work\tasks`, TaskUser: "task", TaskPasswordFile: `C:\work\tasks\secret`}
	if validatePlatformConfig(&c) == nil {
		t.Fatal("task can read its service credential")
	}
}
