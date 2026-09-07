//go:build !windows && !darwin && !linux

package main

import "errors"

func platformToken() string         { return "" }
func desktopSession() error         { return errors.New("unsupported OS") }
func platformCall(request) response { return response{Error: "unsupported OS"} }
func native(request) response       { return response{Error: "unsupported OS"} }
