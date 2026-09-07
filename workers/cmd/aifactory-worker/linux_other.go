//go:build !linux

package main

import "context"

func validateLinuxConfig(c *config) error   { return nil }
func (w *worker) linuxInfo() map[string]any { return nil }
func (w *worker) executeLinux(context.Context, operation, *logWriter) result {
	return result{Status: "uncertain"}
}
