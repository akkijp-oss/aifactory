//go:build !windows

package main

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"os/signal"
	"runtime"
	"syscall"
)

func replaceFile(from, to string) error { return os.Rename(from, to) }
func lockFile(f *os.File) error         { return syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) }
func syncDirectory(path string) error {
	d, err := os.Open(path)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}
func configureProcess(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
}
func softnetReady() bool {
	path, err := exec.LookPath("softnet")
	if err != nil {
		return false
	}
	info, err := os.Stat(path)
	if err != nil {
		return false
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	return ok && stat.Uid == 0 && info.Mode()&os.ModeSetuid != 0
}
func validatePlatformConfig(c *config) error {
	if runtime.GOOS == "linux" && c.WorkRoot != "" {
		return validateLinuxConfig(c)
	}
	if c.WorkRoot != "" || c.PowerShell != "" || c.TaskUser != "" || c.TaskPasswordFile != "" {
		return errors.New("Windows configuration is not supported on this OS")
	}
	return nil
}
func (w *worker) platformInfo() map[string]any {
	if runtime.GOOS == "linux" && w.c.WorkRoot != "" {
		return w.linuxInfo()
	}
	return nil
}
func (w *worker) executeWindows(context.Context, operation, *logWriter) result {
	return result{Status: "uncertain"}
}
func workerContext() (context.Context, context.CancelFunc) {
	return signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
}
func platformMain() bool { return false }
