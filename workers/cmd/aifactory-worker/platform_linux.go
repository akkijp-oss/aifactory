//go:build linux

package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

func validateLinuxConfig(c *config) error {
	if c.Tart != "" || c.GuestVM != "" || c.BaseVM != "" || c.PowerShell != "" || c.TaskPasswordFile != "" {
		return errors.New("standalone Linux does not accept Tart or Windows settings")
	}
	p := filepath.Clean(c.WorkRoot)
	if !filepath.IsAbs(p) || len(strings.Split(strings.Trim(p, "/"), "/")) < 3 {
		return errors.New("work_root must be a dedicated absolute directory")
	}
	for _, private := range []string{c.StateDir, c.TokenFile, c.CAFile} {
		if private == p || strings.HasPrefix(filepath.Clean(private), p+"/") {
			return errors.New("worker state and credentials must be outside work_root")
		}
	}
	if !nameRE.MatchString(c.TaskUser) {
		return errors.New("dedicated task_user required")
	}
	c.WorkRoot = p
	return nil
}
func (w *worker) linuxInfo() map[string]any {
	return map[string]any{"os": "linux", "arch": runtime.GOARCH, "version": version, "protocol": 1, "mode": "guest", "lifecycle": true, "base_ready": true, "network_ready": true, "isolation": "dedicated-linux-instance", "reset": "workspace", "work_root": w.c.WorkRoot, "pid": os.Getpid()}
}
func noLinuxSymlink(path string) error {
	for p := filepath.Clean(path); ; p = filepath.Dir(p) {
		info, e := os.Lstat(p)
		if e != nil {
			return e
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return errors.New("symlink ancestor refused")
		}
		if p == filepath.Dir(p) {
			return nil
		}
	}
}
func (w *worker) executeLinux(ctx context.Context, op operation, lw *logWriter) result {
	uncertain := result{Status: "uncertain"}
	if os.Geteuid() != 0 || !nameRE.MatchString(op.Payload.Lease) || noLinuxSymlink(w.c.WorkRoot) != nil {
		return uncertain
	}
	account, e := user.Lookup(w.c.TaskUser)
	if e != nil {
		return uncertain
	}
	uid, e := strconv.Atoi(account.Uid)
	if e != nil || uid == 0 {
		return uncertain
	}
	gid, e := strconv.Atoi(account.Gid)
	if e != nil {
		return uncertain
	}
	work := filepath.Join(w.c.WorkRoot, op.Payload.Lease)
	if op.Kind == "guest-prepare" {
		if _, e = os.Lstat(w.leasePath()); !os.IsNotExist(e) {
			return uncertain
		}
		if _, e = os.Lstat(work); !os.IsNotExist(e) {
			return uncertain
		}
		if e = writeAtomic(w.leasePath(), []byte(op.Payload.Lease)); e != nil {
			return uncertain
		}
		if e = os.Mkdir(work, 0700); e != nil {
			return uncertain
		}
		if e = os.Chown(work, uid, gid); e != nil {
			return uncertain
		}
		fmt.Fprintln(lw, "Linux workspace prepared; lease="+op.Payload.Lease)
	} else {
		if !w.ownsLease(op.Payload.Lease) || noLinuxSymlink(work) != nil {
			return uncertain
		}
		switch op.Kind {
		case "guest-release":
			// OpenRoot anchors removal beneath the administrator-owned work root.
			root, e := os.OpenRoot(w.c.WorkRoot)
			if e != nil {
				return uncertain
			}
			defer root.Close()
			if e = root.RemoveAll(op.Payload.Lease); e != nil {
				return uncertain
			}
			if e = os.Remove(w.leasePath()); e != nil {
				return uncertain
			}
			if e = syncDirectory(w.c.StateDir); e != nil {
				return uncertain
			}
			fmt.Fprintln(lw, "Linux workspace removed; lease="+op.Payload.Lease)
		case "guest-exec":
			if op.Payload.Timeout < 1 || op.Payload.Timeout > 3600 {
				return uncertain
			}
			code, e := runLinuxUnit(ctx, op, work, account, lw)
			if e != nil {
				fmt.Fprintln(lw, "Linux operation uncertain: "+e.Error())
				return uncertain
			}
			status := "failed"
			if code == 0 {
				status = "succeeded"
			}
			return result{Status: status, ExitCode: &code}
		default:
			return uncertain
		}
	}
	zero := 0
	return result{Status: "succeeded", ExitCode: &zero}
}

// A transient system service owns the entire process cgroup. Killing the local
// systemd-run client alone does not prove that its remote service has stopped.
func runLinuxUnit(ctx context.Context, op operation, work string, account *user.User, lw *logWriter) (int, error) {
	if !nameRE.MatchString(op.ID) {
		return 0, errors.New("invalid operation ID")
	}
	unit := "aifactory-op-" + op.ID + ".service"
	commandCtx, cancel := context.WithTimeout(ctx, time.Duration(op.Payload.Timeout+10)*time.Second)
	defer cancel()
	args := []string{"--quiet", "--wait", "--pipe", "--collect", "--service-type=exec", "--expand-environment=no", "--unit=" + unit, "--uid=" + account.Uid, "--gid=" + account.Gid,
		"--property=KillMode=control-group", "--property=TimeoutStopSec=5s", "--property=RuntimeMaxSec=" + strconv.Itoa(op.Payload.Timeout) + "s",
		"--property=NoNewPrivileges=yes", "--property=WorkingDirectory=" + work, "--setenv=HOME=" + work, "--setenv=USER=" + account.Username, "--setenv=LOGNAME=" + account.Username,
		"--setenv=PATH=/usr/local/bin:/usr/bin:/bin", "--setenv=LANG=C.UTF-8", "--setenv=LC_ALL=C.UTF-8", "--", "/bin/bash", "--noprofile", "--norc", "-c", op.Payload.Command}
	if os.Getenv("INVOCATION_ID") != "" {
		// Stop sibling operation units even if the installed worker is SIGKILLed.
		args = append([]string{"--property=BindsTo=aifactory-worker.service", "--property=After=aifactory-worker.service"}, args...)
	}
	cmd := exec.CommandContext(commandCtx, "/usr/bin/systemd-run", args...)
	cmd.Stdin = strings.NewReader(op.Input)
	cmd.Stdout = lw
	cmd.Stderr = lw
	cmd.WaitDelay = 2 * time.Second
	err := cmd.Run()
	stopCtx, stopCancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer stopCancel()
	_ = exec.CommandContext(stopCtx, "/usr/bin/systemctl", "stop", unit).Run()
	state, stateErr := exec.CommandContext(stopCtx, "/usr/bin/systemctl", "show", unit, "--property=ActiveState", "--value").Output()
	if stateErr != nil || (strings.TrimSpace(string(state)) != "inactive" && strings.TrimSpace(string(state)) != "failed") {
		return 0, errors.New("service shutdown could not be verified")
	}
	if commandCtx.Err() != nil {
		return 0, commandCtx.Err()
	}
	if err == nil {
		return 0, nil
	}
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		return exit.ExitCode(), nil
	}
	return 0, err
}
