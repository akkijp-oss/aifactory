// aifactory-computer exposes bounded desktop actions as a local CLI and MCP tool.
package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type request struct {
	Action string   `json:"action"`
	X      *int     `json:"x,omitempty"`
	Y      *int     `json:"y,omitempty"`
	Button string   `json:"button,omitempty"`
	Count  int      `json:"count,omitempty"`
	Text   string   `json:"text,omitempty"`
	Keys   []string `json:"keys,omitempty"`
	Amount int      `json:"amount,omitempty"`
}

var artifactDir string

type response struct {
	OK     bool   `json:"ok"`
	Error  string `json:"error,omitempty"`
	Path   string `json:"path,omitempty"`
	Image  string `json:"image,omitempty"`
	MIME   string `json:"mimeType,omitempty"`
	Width  int    `json:"width,omitempty"`
	Height int    `json:"height,omitempty"`
}

func validate(r request) error {
	switch r.Action {
	case "screenshot":
	case "click", "move":
		if r.X == nil || r.Y == nil || *r.X < 0 || *r.Y < 0 || *r.X > 16384 || *r.Y > 16384 {
			return errors.New("x and y must be screenshot pixel coordinates")
		}
		if r.Button != "" && r.Button != "left" && r.Button != "right" {
			return errors.New("invalid button")
		}
		if r.Count < 0 || r.Count > 2 {
			return errors.New("invalid click count")
		}
	case "type":
		if len(r.Text) > 8192 {
			return errors.New("text exceeds 8192 bytes")
		}
	case "key":
		if len(r.Keys) < 1 || len(r.Keys) > 4 {
			return errors.New("provide 1 to 4 keys")
		}
		for _, k := range r.Keys {
			if len(k) > 12 {
				return errors.New("invalid key")
			}
		}
	case "scroll":
		if r.Amount == 0 || r.Amount < -20 || r.Amount > 20 {
			return errors.New("scroll amount must be -20..20 excluding zero")
		}
	default:
		return errors.New("unsupported action")
	}
	return nil
}
func decode(b []byte) (request, error) {
	var r request
	d := json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	if e := d.Decode(&r); e != nil {
		return r, e
	}
	if d.Decode(new(any)) != io.EOF {
		return r, errors.New("trailing JSON")
	}
	return r, validate(r)
}
func defaultToken() string { return platformToken() }
func call(r request) response {
	if e := validate(r); e != nil {
		return response{Error: e.Error()}
	}
	return platformCall(r)
}
func serve(tokenPath string) error {
	if e := desktopSession(); e != nil {
		return e
	}
	b, e := os.ReadFile(tokenPath)
	if e != nil {
		return e
	}
	token := strings.TrimSpace(string(b))
	if len(token) < 32 {
		return errors.New("invalid desktop token")
	}
	listener, e := net.Listen("tcp", "127.0.0.1:31191")
	if e != nil {
		return e
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, h *http.Request) {
		if h.Method != "POST" || h.URL.Path != "/action" {
			http.Error(w, "not found", 404)
			return
		}
		if subtle.ConstantTimeCompare([]byte(h.Header.Get("Authorization")), []byte("Bearer "+token)) != 1 {
			http.Error(w, "unauthorized", 401)
			return
		}
		b, e := io.ReadAll(io.LimitReader(h.Body, 32769))
		if e != nil || len(b) > 32768 {
			http.Error(w, "request too large", 413)
			return
		}
		r, e := decode(b)
		if e != nil {
			http.Error(w, e.Error(), 400)
			return
		}
		result := native(r)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(result)
	})
	// Serialize GUI actions; a desktop has only one pointer and focus.
	gate := make(chan struct{}, 1)
	serial := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case gate <- struct{}{}:
			defer func() { <-gate }()
			handler.ServeHTTP(w, r)
		case <-r.Context().Done():
		}
	})
	return (&http.Server{Handler: serial, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 30 * time.Second, MaxHeaderBytes: 8192}).Serve(listener)
}
func remote(r request) response {
	b, e := os.ReadFile(defaultToken())
	if e != nil {
		return response{Error: "desktop agent not configured"}
	}
	data, _ := json.Marshal(r)
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, "POST", "http://127.0.0.1:31191/action", bytes.NewReader(data))
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(string(b)))
	client := http.Client{Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect refused") }}
	res, e := client.Do(req)
	if e != nil {
		return response{Error: "desktop agent unavailable"}
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return response{Error: fmt.Sprintf("desktop agent HTTP %d", res.StatusCode)}
	}
	var out response
	if e = json.NewDecoder(io.LimitReader(res.Body, 12*1024*1024)).Decode(&out); e != nil {
		return response{Error: "invalid desktop response"}
	}
	return out
}
func schema() map[string]any {
	return map[string]any{"type": "object", "additionalProperties": false, "required": []string{"action"}, "properties": map[string]any{
		"action": map[string]any{"type": "string", "enum": []string{"screenshot", "click", "move", "type", "key", "scroll"}}, "x": map[string]string{"type": "integer"}, "y": map[string]string{"type": "integer"}, "button": map[string]any{"type": "string", "enum": []string{"left", "right"}}, "count": map[string]string{"type": "integer"}, "text": map[string]string{"type": "string"}, "keys": map[string]any{"type": "array", "items": map[string]string{"type": "string"}}, "amount": map[string]string{"type": "integer"}}}
}
func mcp() {
	decoder := json.NewDecoder(io.LimitReader(os.Stdin, 32*1024*1024))
	encoder := json.NewEncoder(os.Stdout)
	for {
		var msg struct {
			ID     json.RawMessage `json:"id"`
			Method string          `json:"method"`
			Params struct {
				Name      string          `json:"name"`
				Arguments json.RawMessage `json:"arguments"`
			} `json:"params"`
		}
		if decoder.Decode(&msg) != nil {
			return
		}
		if len(msg.ID) == 0 {
			continue
		}
		var result any
		switch msg.Method {
		case "initialize":
			result = map[string]any{"protocolVersion": "2024-11-05", "capabilities": map[string]any{"tools": map[string]any{}}, "serverInfo": map[string]string{"name": "aifactory-computer", "version": "0.1.0"}}
		case "ping":
			result = map[string]any{}
		case "tools/list":
			result = map[string]any{"tools": []any{map[string]any{"name": "computer", "description": "Operate this dedicated VM desktop. Screenshot first; x/y are pixels in that image. key takes 1-4 of these names (case-insensitive, same on every OS): CTRL ALT SHIFT WIN CMD ENTER TAB ESC SPACE BACKSPACE DELETE LEFT RIGHT UP DOWN HOME END PAGEUP PAGEDOWN F1-F12 A-Z 0-9 and the US-layout symbols = - + , . / ; ' [ ] \\ ` (WIN and CMD both mean the local meta key; + is SHIFT and =). type sends Unicode text. Positive scroll moves up. Desktop must be unlocked. Never operate a host desktop.", "inputSchema": schema()}}}
		case "tools/call":
			r, e := decode(msg.Params.Arguments)
			var out response
			if e != nil {
				out.Error = e.Error()
			} else if msg.Params.Name != "computer" {
				out.Error = "unknown tool"
			} else {
				out = call(r)
			}
			if out.OK && out.Image != "" && artifactDir != "" {
				data, err := base64.StdEncoding.DecodeString(out.Image)
				if err != nil || len(data) > 8*1024*1024 {
					out = response{Error: "invalid screenshot"}
				} else {
					path := filepath.Join(artifactDir, "screenshot-latest.png")
					temp, err := os.CreateTemp(artifactDir, ".screenshot-*.png")
					if err != nil {
						out = response{Error: "cannot save screenshot artifact"}
					} else {
						name := temp.Name()
						_, err = temp.Write(data)
						closeErr := temp.Close()
						if err == nil {
							err = closeErr
						}
						if err == nil {
							err = os.Rename(name, path)
						}
						if err != nil {
							os.Remove(name)
							out = response{Error: "cannot save screenshot artifact"}
						} else {
							out.Path = path
						}
					}
				}
			}
			contents := []any{}
			if out.Image != "" {
				contents = append(contents, map[string]any{"type": "image", "mimeType": "image/png", "data": out.Image})
				out.Image = ""
			}
			b, _ := json.Marshal(out)
			contents = append(contents, map[string]any{"type": "text", "text": string(b)})
			result = map[string]any{"content": contents, "isError": !out.OK}
		default:
			encoder.Encode(map[string]any{"jsonrpc": "2.0", "id": msg.ID, "error": map[string]any{"code": -32601, "message": "unknown method"}})
			continue
		}
		encoder.Encode(map[string]any{"jsonrpc": "2.0", "id": msg.ID, "result": result})
	}
}
func main() {
	flag.StringVar(&artifactDir, "artifacts", "", "directory to save MCP screenshots")
	mode := flag.String("mode", "request", "request, mcp, serve, init-token")
	token := flag.String("token", defaultToken(), "local agent credential path")
	flag.Parse()
	switch *mode {
	case "mcp":
		mcp()
	case "serve":
		if e := serve(*token); e != nil {
			fmt.Fprintln(os.Stderr, e)
			os.Exit(1)
		}
	case "init-token":
		if e := os.MkdirAll(filepath.Dir(*token), 0700); e != nil {
			os.Exit(1)
		}
		b := make([]byte, 32)
		if _, e := rand.Read(b); e != nil {
			os.Exit(1)
		}
		f, e := os.OpenFile(*token, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if e != nil {
			fmt.Fprintln(os.Stderr, e)
			os.Exit(1)
		}
		f.WriteString(hex.EncodeToString(b))
		f.Close()
	case "request":
		b, e := io.ReadAll(io.LimitReader(os.Stdin, 32769))
		var out response
		if e != nil || len(b) > 32768 {
			out.Error = "invalid input"
		} else {
			r, e := decode(b)
			if e != nil {
				out.Error = e.Error()
			} else {
				out = call(r)
			}
		}
		json.NewEncoder(os.Stdout).Encode(out)
		if !out.OK {
			os.Exit(1)
		}
	default:
		os.Exit(2)
	}
}
