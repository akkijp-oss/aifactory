//go:build windows

package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"time"
	"unicode/utf16"
	"unsafe"

	"golang.org/x/sys/windows"
)

func lockFile(f *os.File) error {
	return windows.LockFileEx(windows.Handle(f.Fd()), windows.LOCKFILE_EXCLUSIVE_LOCK|windows.LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, &windows.Overlapped{})
}
func syncDirectory(string) error { return nil } // Windows uses write-through rename instead of directory fsync.
func replaceFile(from, to string) error {
	a, e := windows.UTF16PtrFromString(from)
	if e != nil {
		return e
	}
	b, e := windows.UTF16PtrFromString(to)
	if e != nil {
		return e
	}
	return windows.MoveFileEx(a, b, windows.MOVEFILE_REPLACE_EXISTING|windows.MOVEFILE_WRITE_THROUGH)
}
func configureProcess(*exec.Cmd) {} // Tart execution is unreachable on Windows.
func softnetReady() bool         { return false }
func validatePlatformConfig(c *config) error {
	if c.GuestVM != "" || c.BaseVM != "" || c.Tart != "" {
		return errors.New("Windows worker runs inside a dedicated VM; Tart settings are invalid")
	}
	if c.WorkRoot != "" {
		p := filepath.Clean(c.WorkRoot)
		if !filepath.IsAbs(p) || strings.HasPrefix(p, `\\`) || len(strings.TrimPrefix(p, filepath.VolumeName(p))) < 4 {
			return errors.New("work_root must be a dedicated absolute local directory")
		}
		c.WorkRoot = p
		if !nameRE.MatchString(c.TaskUser) || !filepath.IsAbs(c.TaskPasswordFile) {
			return errors.New("dedicated task_user and absolute task_password_file required")
		}
		if strings.HasPrefix(strings.ToLower(filepath.Clean(c.TaskPasswordFile)), strings.ToLower(p)+string(os.PathSeparator)) {
			return errors.New("task credentials must be outside work_root")
		}

	}
	if c.PowerShell == "" {
		c.PowerShell = filepath.Join(os.Getenv("SystemRoot"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
	}
	if !filepath.IsAbs(c.PowerShell) {
		return errors.New("powershell must be an absolute path")
	}
	return nil
}
func (w *worker) platformInfo() map[string]any {
	mode := "probe-only"
	if w.c.WorkRoot != "" {
		mode = "guest"
	}
	return map[string]any{"os": runtime.GOOS, "arch": runtime.GOARCH, "version": version, "protocol": 1, "mode": mode, "lifecycle": w.c.WorkRoot != "", "base_ready": true, "network_ready": true, "isolation": "dedicated-windows-vm", "reset": "workspace", "work_root": w.c.WorkRoot, "pid": os.Getpid()}
}

func noReparse(path string) error {
	p := filepath.Clean(path)
	for {
		name, e := windows.UTF16PtrFromString(p)
		if e != nil {
			return e
		}
		attrs, e := windows.GetFileAttributes(name)
		if e != nil {
			return e
		}
		if attrs&windows.FILE_ATTRIBUTE_REPARSE_POINT != 0 {
			return errors.New("reparse point refused")
		}
		parent := filepath.Dir(p)
		if parent == p {
			return nil
		}
		p = parent
	}
}

func (w *worker) executeWindows(ctx context.Context, op operation, lw *logWriter) result {
	r := result{Status: "uncertain"}
	if w.c.WorkRoot == "" || !nameRE.MatchString(op.Payload.Lease) || noReparse(w.c.WorkRoot) != nil {
		return r
	}
	work := filepath.Join(w.c.WorkRoot, op.Payload.Lease)
	if op.Kind == "guest-prepare" {
		if _, e := os.Stat(w.leasePath()); !os.IsNotExist(e) {
			return r
		}
		if _, e := os.Lstat(work); !os.IsNotExist(e) {
			return r
		}
		if e := writeAtomic(w.leasePath(), []byte(op.Payload.Lease)); e != nil {
			return r
		}
		if e := os.Mkdir(work, 0700); e != nil {
			return r
		}
		fmt.Fprintln(lw, "Windows workspace prepared; lease="+op.Payload.Lease)
	} else {
		if !w.ownsLease(op.Payload.Lease) || noReparse(work) != nil {
			return r
		}
		switch op.Kind {
		case "guest-release":
			// Never traverse a junction/symlink introduced by a task during cleanup.
			e := filepath.WalkDir(work, func(p string, d os.DirEntry, e error) error {
				if e != nil {
					return e
				}
				return noReparse(p)
			})
			if e != nil {
				return r
			}
			if e = os.RemoveAll(work); e != nil {
				return r
			}
			if e = os.Remove(w.leasePath()); e != nil {
				return r
			}
			fmt.Fprintln(lw, "Windows workspace removed; lease="+op.Payload.Lease)
		case "guest-exec":
			if op.Payload.Timeout < 1 || op.Payload.Timeout > 3600 {
				return r
			}
			commandCtx, cancel := context.WithTimeout(ctx, time.Duration(op.Payload.Timeout)*time.Second)
			defer cancel()
			script := "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); $OutputEncoding=[Console]::OutputEncoding; " + op.Payload.Command
			token, e := taskLogon(w.c.TaskUser, w.c.TaskPasswordFile)
			if e != nil {
				fmt.Fprintln(lw, "cannot log on dedicated task account")
				return r
			}
			defer token.Close()
			code, e := runWindowsJob(commandCtx, token, childRequest{Program: w.c.PowerShell, Args: []string{"-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encodePowerShell(script)}, Directory: work, Input: op.Input}, lw)
			if e != nil {
				fmt.Fprintln(lw, "Windows process job failed: "+e.Error())
				return r
			}
			if commandCtx.Err() != nil {
				if ctx.Err() != nil {
					r.Status = "cancelled"
				}
				return r
			}
			r.ExitCode = &code
			r.Status = "failed"
			if code == 0 {
				r.Status = "succeeded"
			}
			return r
		default:
			return r
		}
	}
	zero := 0
	return result{Status: "succeeded", ExitCode: &zero}
}

func encodePowerShell(s string) string {
	u := utf16.Encode([]rune(s))
	b := make([]byte, 2*len(u))
	for i, v := range u {
		b[2*i] = byte(v)
		b[2*i+1] = byte(v >> 8)
	}
	return base64.StdEncoding.EncodeToString(b)
}

type childRequest struct {
	Program   string
	Args      []string
	Directory string
	Input     string
}

// The helper reads no task until it has been assigned to the job. If the worker
// dies before assignment, its input pipe closes and the helper exits. After
// assignment, KILL_ON_JOB_CLOSE covers the helper and every descendant.
func runWindowsJob(ctx context.Context, token windows.Token, request childRequest, out io.Writer) (int, error) {
	job, e := windows.CreateJobObject(nil, nil)
	if e != nil {
		return 0, e
	}
	defer windows.CloseHandle(job)
	limits := windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION{}
	limits.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	if _, e = windows.SetInformationJobObject(job, windows.JobObjectExtendedLimitInformation, uintptr(unsafe.Pointer(&limits)), uint32(unsafe.Sizeof(limits))); e != nil {
		return 0, e
	}
	exe, e := os.Executable()
	if e != nil {
		return 0, e
	}
	cmd := exec.Command(exe, "--job-child")
	cmd.SysProcAttr = &syscall.SysProcAttr{Token: syscall.Token(token)}
	cmd.WaitDelay = 2 * time.Second
	cmd.Stdout = out
	cmd.Stderr = out
	input, e := cmd.StdinPipe()
	if e != nil {
		return 0, e
	}
	defer input.Close()
	if e = cmd.Start(); e != nil {
		return 0, e
	}
	process, e := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE, false, uint32(cmd.Process.Pid))
	if e != nil {
		cmd.Process.Kill()
		cmd.Wait()
		return 0, e
	}
	e = windows.AssignProcessToJobObject(job, process)
	windows.CloseHandle(process)
	if e != nil {
		cmd.Process.Kill()
		cmd.Wait()
		return 0, e
	}
	if e = json.NewEncoder(input).Encode(request); e != nil {
		windows.TerminateJobObject(job, 1)
		cmd.Wait()
		return 0, e
	}
	input.Close()
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case <-ctx.Done():
		if e = windows.TerminateJobObject(job, 1); e != nil {
			return 0, e
		}
		<-done
	case e = <-done:
	}
	// A successful parent may leave background descendants: terminate them too.
	if stopErr := windows.TerminateJobObject(job, 1); stopErr != nil {
		return 0, stopErr
	}
	for i := 0; i < 100; i++ {
		var info jobAccounting
		e2 := windows.QueryInformationJobObject(job, windows.JobObjectBasicAccountingInformation, uintptr(unsafe.Pointer(&info)), uint32(unsafe.Sizeof(info)), nil)
		if e2 != nil {
			return 0, e2
		}
		if info.ActiveProcesses == 0 {
			if e != nil {
				var exit *exec.ExitError
				if !errors.As(e, &exit) && !errors.Is(e, exec.ErrWaitDelay) {
					return 0, e
				}
			}
			return cmd.ProcessState.ExitCode(), nil
		}
		time.Sleep(50 * time.Millisecond)
	}
	return 0, errors.New("Windows job still has active processes")
}

func jobChild() {
	var r childRequest
	if e := json.NewDecoder(io.LimitReader(os.Stdin, 1024*1024)).Decode(&r); e != nil {
		os.Exit(125)
	}
	cmd := exec.Command(r.Program, r.Args...)
	cmd.Dir = r.Directory
	profile := filepath.Join(r.Directory, ".profile")
	for _, dir := range []string{profile, filepath.Join(profile, "AppData", "Roaming"), filepath.Join(profile, "AppData", "Local"), filepath.Join(profile, "Temp")} {
		if err := os.MkdirAll(dir, 0700); err != nil {
			os.Exit(126)
		}
	}
	cmd.Env = os.Environ()
	for key, value := range map[string]string{"HOME": profile, "USERPROFILE": profile, "APPDATA": filepath.Join(profile, "AppData", "Roaming"), "LOCALAPPDATA": filepath.Join(profile, "AppData", "Local"), "TEMP": filepath.Join(profile, "Temp"), "TMP": filepath.Join(profile, "Temp")} {
		cmd.Env = append(cmd.Env, key+"="+value)
	}
	cmd.Stdin = strings.NewReader(r.Input)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if e := cmd.Run(); e != nil {
		var exit *exec.ExitError
		if errors.As(e, &exit) {
			os.Exit(exit.ExitCode())
		}
		os.Exit(126)
	}
	os.Exit(0)
}

var serviceContext context.Context

func workerContext() (context.Context, context.CancelFunc) {
	if serviceContext != nil {
		return context.WithCancel(serviceContext)
	}
	return signal.NotifyContext(context.Background(), os.Interrupt)
}

// JOBOBJECT_BASIC_ACCOUNTING_INFORMATION (Win32 ABI).
type jobAccounting struct {
	TotalUserTime             int64
	TotalKernelTime           int64
	ThisPeriodTotalUserTime   int64
	ThisPeriodTotalKernelTime int64
	TotalPageFaultCount       uint32
	TotalProcesses            uint32
	ActiveProcesses           uint32
	TotalTerminatedProcesses  uint32
}

// The service owns queue credentials; task processes use a separate ordinary
// local account, which cannot read the service configuration or journal.
func taskLogon(user, passwordFile string) (windows.Token, error) {
	b, e := os.ReadFile(passwordFile)
	if e != nil {
		return 0, e
	}
	defer clear(b)
	username, e := windows.UTF16PtrFromString(user)
	if e != nil {
		return 0, e
	}
	domain, _ := windows.UTF16PtrFromString(".")
	password, e := windows.UTF16FromString(strings.TrimSpace(string(b)))
	if e != nil {
		return 0, e
	}
	defer clear(password)
	var token windows.Token
	result, _, callErr := windows.NewLazySystemDLL("advapi32.dll").NewProc("LogonUserW").Call(uintptr(unsafe.Pointer(username)), uintptr(unsafe.Pointer(domain)), uintptr(unsafe.Pointer(&password[0])), 4, 0, uintptr(unsafe.Pointer(&token)))
	if result == 0 {
		return 0, callErr
	}
	return token, nil
}
