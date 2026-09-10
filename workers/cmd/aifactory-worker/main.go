// aifactory-worker receives work over outbound HTTPS on dedicated execution machines.
package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"
)

const maxLog = 16 * 1024 * 1024
const maxLogChunk = 16384

// Output without a newline cannot buffer forever; beyond this the line is written as it is.
const maxPendingLine = 8 * 1024 * 1024
const truncationNotice = "\n[operation log truncated at 16 MiB; later output was discarded]\n"
const version = "0.3.1"

var nameRE = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`)

// A screenshot tool_result carries roughly a megabyte of base64 per stream-json line, which
// alone exhausts the log quota. The image stays on the guest as a file, so the log only needs
// its size. Matching leaves the line valid JSON, which the runner's renderer relies on.
// 1000 is the largest repeat count Go's regexp accepts; 750 decoded bytes is already far
// beyond any ordinary field, and a false positive only costs a payload the guest still has.
var base64DataRE = regexp.MustCompile(`"data":"[A-Za-z0-9+/]{1000,}={0,2}"`)

func stripBase64Data(text string) string {
	if !strings.Contains(text, `"type":"base64"`) {
		return text
	}
	return base64DataRE.ReplaceAllStringFunc(text, func(m string) string {
		encoded := m[len(`"data":"`) : len(m)-1]
		size := len(encoded) / 4 * 3
		for i := len(encoded) - 1; i >= 0 && encoded[i] == '='; i-- {
			size--
		}
		return fmt.Sprintf(`"data":"[image %d bytes]"`, size)
	})
}

type config struct {
	Worker           string `json:"worker"`
	URL              string `json:"url"`
	TokenFile        string `json:"token_file"`
	CAFile           string `json:"ca_file"`
	StateDir         string `json:"state_dir"`
	GuestVM          string `json:"guest_vm"`
	Tart             string `json:"tart"`
	BaseVM           string `json:"base_vm"`
	WorkRoot         string `json:"work_root"`
	PowerShell       string `json:"powershell"`
	TaskUser         string `json:"task_user"`
	TaskPasswordFile string `json:"task_password_file"`
	// Guest display resolution for every PJ on this worker. Absent leaves the base image as it is.
	Display *displaySize `json:"display"`
}

type displaySize struct {
	Width  int `json:"width"`
	Height int `json:"height"`
}

// The range project.schema.json and the control plane both enforce; a worker
// configured outside it refuses to start rather than passing tart a bad size.
func validDisplay(width, height int) bool {
	return 800 <= width && width <= 2560 && 600 <= height && height <= 2560
}

type operation struct {
	ID      string `json:"id"`
	Kind    string `json:"kind"`
	Input   string `json:"stdin"`
	Payload struct {
		Command string `json:"command"`
		Timeout int    `json:"timeout"`
		Lease   string `json:"lease"`
		Width   int    `json:"width"`
		Height  int    `json:"height"`
	} `json:"payload"`
	Cancelled int `json:"cancelled"`
}

type result struct {
	Status    string `json:"status"`
	ExitCode  *int   `json:"exit_code"`
	Events    int    `json:"events"`
	Truncated bool   `json:"truncated,omitempty"`
}

// Journal files are fsynced before publication. A start marker without a result
// after restart is uncertain and is never automatically executed again.
type journal struct {
	root string
	lock *os.File
}

func writeAtomic(path string, data []byte) error {
	f, err := os.CreateTemp(filepath.Dir(path), ".pending-")
	if err != nil {
		return err
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if _, err = f.Write(data); err != nil {
		f.Close()
		return err
	}
	if err = f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	if err = replaceFile(tmp, path); err != nil {
		return err
	}
	return syncDirectory(filepath.Dir(path))
}

func newJournal(root string) (*journal, error) {
	if !filepath.IsAbs(root) {
		return nil, errors.New("state_dir must be absolute")
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(filepath.Join(root, "worker.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	if err = lockFile(f); err != nil {
		f.Close()
		return nil, errors.New("another worker owns this state directory")
	}
	return &journal{root: root, lock: f}, nil
}

func (j *journal) dir(id string) string { return filepath.Join(j.root, id) }
func (j *journal) begin(id string) (bool, error) {
	if !nameRE.MatchString(id) {
		return false, errors.New("invalid operation ID")
	}
	err := os.Mkdir(j.dir(id), 0700)
	if errors.Is(err, os.ErrExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	// Persist the parent directory as well as the start marker.
	if err = writeAtomic(filepath.Join(j.dir(id), "started"), []byte(id)); err != nil {
		return false, err
	}
	return true, syncDirectory(j.root)
}
func (j *journal) event(id string, seq int) string {
	return filepath.Join(j.dir(id), fmt.Sprintf("event-%08d.json", seq))
}
func (j *journal) count(id string) int {
	for n := 0; ; n++ {
		if _, err := os.Stat(j.event(id, n)); err != nil {
			return n
		}
	}
}
func (j *journal) finish(id string, r result) error {
	r.Events = j.count(id)
	b, err := json.Marshal(r)
	if err != nil {
		return err
	}
	return writeAtomic(filepath.Join(j.dir(id), "result.json"), b)
}

type logWriter struct {
	mu        sync.Mutex
	j         *journal
	id        string
	seq, size int
	pending   []byte
	truncated bool
}

// Write buffers whole lines so that a stream-json event is rewritten as a unit, then hands
// them to emit. Reaching the log quota truncates rather than failing: returning an error here
// breaks the pipe to tart exec, which leaves the guest command running with nowhere to write
// and forces the operation to uncertain even though its exit code is still obtainable.
func (w *logWriter) Write(b []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	n := len(b)
	if w.truncated {
		return n, nil
	}
	w.pending = append(w.pending, b...)
	// pending never retains a newline, so only the bytes just appended can complete a line.
	cut := -1
	if i := bytes.LastIndexByte(b, '\n'); i >= 0 {
		cut = len(w.pending) - len(b) + i + 1
	}
	if cut < 0 {
		if len(w.pending) < maxPendingLine {
			return n, nil
		}
		cut = len(w.pending)
		// A forced cut must not split a multi-byte character.
		for i := 1; i < utf8.UTFMax && i < cut; i++ {
			if utf8.RuneStart(w.pending[cut-i]) {
				if !utf8.FullRune(w.pending[cut-i:]) {
					cut -= i
				}
				break
			}
		}
	}
	ready := string(w.pending[:cut])
	w.pending = append(w.pending[:0], w.pending[cut:]...)
	return n, w.emit(ready)
}

// emit records text as events of bounded size, never splitting a character across events.
// Only a persistence failure is an error.
func (w *logWriter) emit(text string) error {
	text = strings.ToValidUTF8(stripBase64Data(text), "\uFFFD")
	for len(text) > 0 {
		end := min(len(text), maxLogChunk)
		// Walk complete runes so a chunk ends on a character boundary.
		complete := 0
		for complete < end {
			_, size := utf8.DecodeRuneInString(text[complete:])
			if complete+size > end {
				break
			}
			complete += size
		}
		if complete == 0 {
			complete = end
		}
		chunk := text[:complete]
		// Leave room for the notice so the recorded log still fits the quota.
		if w.size+len(chunk) > maxLog-len(truncationNotice) {
			return w.truncate()
		}
		data, _ := json.Marshal(chunk)
		if err := writeAtomic(w.j.event(w.id, w.seq), data); err != nil {
			return err
		}
		w.seq++
		w.size += len(chunk)
		text = text[complete:]
	}
	return nil
}

// truncate records the quota being reached exactly once; later output is discarded.
func (w *logWriter) truncate() error {
	w.truncated = true
	w.pending = nil
	data, _ := json.Marshal(truncationNotice)
	if err := writeAtomic(w.j.event(w.id, w.seq), data); err != nil {
		return err
	}
	w.seq++
	w.size += len(truncationNotice)
	return nil
}

func (w *logWriter) flush() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.truncated || len(w.pending) == 0 {
		w.pending = nil
		return nil
	}
	text := string(w.pending)
	w.pending = nil
	return w.emit(text)
}

type worker struct {
	c       config
	token   string
	client  *http.Client
	j       *journal
	lastOK  atomic.Int64
	current string
	ack     int
	cancel  context.CancelFunc
	done    chan struct{}
	// Injectable in tests; production always runs through Tart's guest agent.
	command func(context.Context, string, ...string) *exec.Cmd
}

func newWorker(c config) (*worker, error) { return configureWorker(c, true) }

func configureWorker(c config, journalRequired bool) (*worker, error) {
	if err := validatePlatformConfig(&c); err != nil {
		return nil, err
	}
	if !nameRE.MatchString(c.Worker) || (c.GuestVM != "" && !nameRE.MatchString(c.GuestVM)) {
		return nil, errors.New("invalid worker or guest VM name")
	}
	if c.BaseVM != "" && (!nameRE.MatchString(c.BaseVM) || c.GuestVM == "" || c.BaseVM == c.GuestVM) {
		return nil, errors.New("base_vm must be a distinct local VM name")
	}
	if c.Display != nil && !validDisplay(c.Display.Width, c.Display.Height) {
		return nil, errors.New("display width must be 800-2560 and height 600-2560")
	}
	u, err := url.Parse(c.URL)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return nil, errors.New("endpoint must be an HTTPS URL without credentials/query/fragment")
	}
	if !filepath.IsAbs(c.TokenFile) || !filepath.IsAbs(c.CAFile) {
		return nil, errors.New("token_file and ca_file must be absolute")
	}
	token, err := os.ReadFile(c.TokenFile)
	if err != nil {
		return nil, err
	}
	if len(strings.TrimSpace(string(token))) < 32 {
		return nil, errors.New("invalid worker token")
	}
	ca, err := os.ReadFile(c.CAFile)
	if err != nil {
		return nil, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(ca) {
		return nil, errors.New("invalid CA certificate")
	}
	if runtime.GOOS != "windows" && c.WorkRoot == "" && c.Tart == "" {
		c.Tart = "/opt/homebrew/bin/tart"
	}
	if runtime.GOOS != "windows" && c.WorkRoot == "" && !filepath.IsAbs(c.Tart) {
		return nil, errors.New("tart must be an absolute path")
	}
	var j *journal
	if journalRequired {
		j, err = newJournal(c.StateDir)
		if err != nil {
			return nil, err
		}
	}
	client := &http.Client{Timeout: 10 * time.Second,
		Transport:     &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}},
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return errors.New("redirect refused") }}
	w := &worker{c: c, token: strings.TrimSpace(string(token)), client: client, j: j, command: exec.CommandContext}
	w.lastOK.Store(time.Now().UnixNano())
	return w, nil
}

func (w *worker) post(ctx context.Context, endpoint string, data map[string]any, out any) error {
	data["version"] = 1
	body, err := json.Marshal(data)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, "POST", strings.TrimRight(w.c.URL, "/")+"/v1/"+endpoint, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+w.token)
	req.Header.Set("X-Worker-ID", w.c.Worker)
	r, err := w.client.Do(req)
	if err != nil {
		return errors.New("worker endpoint unreachable or TLS verification failed")
	}
	defer r.Body.Close()
	if r.StatusCode != 200 {
		return fmt.Errorf("worker endpoint HTTP %d", r.StatusCode)
	}
	if err = json.NewDecoder(io.LimitReader(r.Body, 1024*1024)).Decode(out); err != nil {
		return err
	}
	w.lastOK.Store(time.Now().UnixNano())
	return nil
}

func (w *worker) info() map[string]any {
	if info := w.platformInfo(); info != nil {
		return info
	}
	mode := "probe-only"
	if w.c.GuestVM != "" {
		mode = "guest"
	}
	baseReady := false
	if w.c.BaseVM != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		exists, running, err := w.vmExists(ctx, w.c.BaseVM)
		cancel()
		baseReady = err == nil && exists && !running
	}
	return map[string]any{"os": runtime.GOOS, "arch": runtime.GOARCH, "version": version, "protocol": 1, "mode": mode, "guest_vm": w.c.GuestVM, "lifecycle": w.c.BaseVM != "", "base_ready": baseReady, "network_ready": softnetReady(), "pid": os.Getpid()}
}

func (w *worker) stopGuest() bool {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	w.command(ctx, w.c.Tart, "stop", w.c.GuestVM, "--timeout", "15").Run()
	for i := 0; i < 10; i++ {
		_, running, err := w.guestExists(ctx)
		if err == nil && !running {
			return true
		}
		if ctx.Err() != nil {
			return false
		}
		time.Sleep(time.Second)
	}
	return false
}

func (w *worker) execute(ctx context.Context, op operation) {
	defer close(w.done)
	ctx, stop := context.WithCancel(ctx)
	defer stop()
	go func() {
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if time.Since(time.Unix(0, w.lastOK.Load())) > 60*time.Second {
					stop()
					return
				}
			}
		}
	}()
	r := result{Status: "uncertain"}
	lw := &logWriter{j: w.j, id: op.ID}
	defer func() {
		// flush only fails when the journal cannot be written, which is genuinely uncertain.
		if err := lw.flush(); err != nil {
			r = result{Status: "uncertain"}
		}
		r.Truncated = lw.truncated
		if err := w.j.finish(op.ID, r); err != nil {
			log.Print("cannot persist operation result; manual recovery required")
		}
	}()
	if op.Cancelled != 0 {
		r.Status = "cancelled"
		return
	}
	if op.Kind == "probe" {
		b, _ := json.Marshal(w.info())
		if _, err := lw.Write(append(b, '\n')); err != nil {
			return
		}
		zero := 0
		r = result{Status: "succeeded", ExitCode: &zero}
		return
	}
	if runtime.GOOS == "linux" && w.c.WorkRoot != "" {
		r = w.executeLinux(ctx, op, lw)
		return
	}
	if runtime.GOOS == "windows" {
		r = w.executeWindows(ctx, op, lw)
		return
	}
	if op.Kind == "guest-prepare" || op.Kind == "guest-release" {
		r = w.lifecycle(ctx, op, lw)
		return
	}
	if w.c.BaseVM != "" && !w.ownsLease(op.Payload.Lease) {
		return
	}
	if op.Kind != "guest-exec" || w.c.GuestVM == "" || op.Payload.Timeout < 1 || op.Payload.Timeout > 3600 {
		return
	}
	cmdCtx, cancel := context.WithTimeout(ctx, time.Duration(op.Payload.Timeout)*time.Second)
	defer cancel()
	args := []string{"exec"}
	if op.Input != "" {
		args = append(args, "-i")
	}
	args = append(args, w.c.GuestVM, "/bin/bash", "-lc", op.Payload.Command)
	cmd := w.command(cmdCtx, w.c.Tart, args...)
	if op.Input != "" {
		cmd.Stdin = strings.NewReader(op.Input)
	}
	cmd.Stdout = lw
	cmd.Stderr = lw
	cmd.WaitDelay = 5 * time.Second
	configureProcess(cmd)
	if err := cmd.Start(); err != nil {
		return
	}
	finished := make(chan error, 1)
	go func() { finished <- cmd.Wait() }()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case err := <-finished:
			if cmdCtx.Err() != nil {
				// Stop the whole guest; killing tart exec does not stop guest processes.
				if w.stopGuest() && errors.Is(ctx.Err(), context.Canceled) {
					r.Status = "cancelled"
				}
				return
			}
			if err != nil {
				var exit *exec.ExitError
				if !errors.As(err, &exit) {
					w.stopGuest()
					return
				}
			}
			code := cmd.ProcessState.ExitCode()
			r.ExitCode = &code
			r.Status = "failed"
			if code == 0 {
				r.Status = "succeeded"
			}
			return
		case <-ticker.C:
			if time.Since(time.Unix(0, w.lastOK.Load())) > 60*time.Second {
				cancel()
			}
		}
	}
}

func (w *worker) tick(ctx context.Context) error {
	var hb struct {
		Cancel []string `json:"cancel"`
	}
	if err := w.post(ctx, "heartbeat", map[string]any{"info": w.info()}, &hb); err != nil {
		return err
	}
	for _, id := range hb.Cancel {
		if id == w.current && w.cancel != nil {
			w.cancel()
		}
	}
	if w.current == "" {
		var reply struct {
			Operation *operation `json:"operation"`
		}
		if err := w.post(ctx, "poll", map[string]any{}, &reply); err != nil {
			return err
		}
		if reply.Operation == nil {
			return nil
		}
		op := *reply.Operation
		fresh, err := w.j.begin(op.ID)
		if err != nil {
			return err
		}
		w.current = op.ID
		w.ack = 0
		if fresh {
			runCtx, cancel := context.WithCancel(context.Background())
			w.cancel = cancel
			w.done = make(chan struct{})
			go w.execute(runCtx, op)
		} else {
			if _, err := os.Stat(filepath.Join(w.j.dir(op.ID), "result.json")); errors.Is(err, os.ErrNotExist) {
				if err = w.j.finish(op.ID, result{Status: "uncertain"}); err != nil {
					return err
				}
			}
		}
	}
	for n := 0; n < 8; n++ {
		b, err := os.ReadFile(w.j.event(w.current, w.ack))
		if errors.Is(err, os.ErrNotExist) {
			break
		}
		if err != nil {
			return err
		}
		var text string
		if err = json.Unmarshal(b, &text); err != nil {
			return err
		}
		var reply struct {
			Ack int `json:"ack"`
		}
		if err = w.post(ctx, "event", map[string]any{"operation": w.current, "seq": w.ack, "text": text}, &reply); err != nil {
			return err
		}
		if reply.Ack != w.ack {
			return errors.New("invalid event acknowledgment")
		}
		w.ack++
	}
	b, err := os.ReadFile(filepath.Join(w.j.dir(w.current), "result.json"))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var r result
	if err = json.Unmarshal(b, &r); err != nil {
		return err
	}
	if w.ack != r.Events {
		return nil
	}
	var reply struct {
		Accepted bool `json:"accepted"`
	}
	if err = w.post(ctx, "complete", map[string]any{"operation": w.current, "result": r}, &reply); err != nil {
		return err
	}
	if !reply.Accepted {
		return errors.New("completion not accepted")
	}
	if w.cancel != nil {
		w.cancel()
		w.cancel = nil
	}
	w.current = ""
	return nil
}

func run() error {
	configPath := flag.String("config", "", "absolute path to private worker JSON configuration")
	showVersion := flag.Bool("version", false, "print version")
	check := flag.Bool("check", false, "verify TLS and worker credentials without polling or changing state")
	flag.Parse()
	if *showVersion {
		fmt.Println("aifactory-worker " + version + " " + runtime.GOOS + "/" + runtime.GOARCH)
		return nil
	}
	b, err := os.ReadFile(*configPath)
	if err != nil {
		return err
	}
	var c config
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.DisallowUnknownFields()
	if err = dec.Decode(&c); err != nil {
		return err
	}
	w, err := configureWorker(c, !*check)
	if err != nil {
		return err
	}
	if *check {
		var verified map[string]any
		if err = w.post(context.Background(), "check", map[string]any{}, &verified); err != nil {
			return err
		}
		fmt.Println("Worker TLS and authentication verified")
		return nil
	}
	defer w.j.lock.Close()
	ctx, cancel := workerContext()
	defer cancel()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for ctx.Err() == nil {
		if err = w.tick(ctx); err != nil && ctx.Err() == nil {
			log.Print(err)
		}
		select {
		case <-ctx.Done():
		case <-ticker.C:
		}
	}
	if w.cancel != nil {
		w.cancel()
	}
	if w.done != nil {
		select {
		case <-w.done:
		case <-time.After(40 * time.Second):
		}
	}
	return nil
}

func main() {
	if handled := platformMain(); handled {
		return
	}
	if err := run(); err != nil {
		log.Print(err)
		os.Exit(1)
	}
}
