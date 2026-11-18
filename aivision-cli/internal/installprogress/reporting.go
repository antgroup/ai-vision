// Package installprogress sends an optional, best-effort progress stream to the
// operator-selected AI Vision installation session. It never controls the host operation.
package installprogress

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
)

const maxQueued = 128
const warning = "Warning: installation progress reporting is unavailable; the host operation continues.\n"

type image struct {
	Layer   string `json:"layer"`
	Status  string `json:"status"`
	Current string `json:"current"`
	Total   string `json:"total"`
}
type event struct {
	Seq        int64                                 `json:"seq"`
	Stage      string                                `json:"stage"`
	State      string                                `json:"state"`
	Detail     string                                `json:"detail"`
	ElapsedMS  int64                                 `json:"elapsed_ms"`
	Image      *image                                `json:"image,omitempty"`
	Diagnostic *sandboxplugin.InstallationDiagnostic `json:"diagnostic,omitempty"`
}
type batch struct {
	Operation string  `json:"operation"`
	RunID     string  `json:"run_id"`
	Events    []event `json:"events"`
}

// Reporter keeps queued updates bounded. Observe only copies trusted, typed
// events under a short mutex; HTTP and retry waits happen in a separate goroutine.
// Sequences are assigned after coalescing, and retained unchanged until acknowledged.
type Reporter struct {
	endpoint, token, operation, runID string
	client                            *http.Client
	warnings                          io.Writer
	warnOnce                          sync.Once
	mu                                sync.Mutex
	pending                           []event
	closed                            bool
	failed                            bool
	inFlight                          bool
	changed                           chan struct{}
	wake                              chan struct{}
	done                              chan struct{}
	ctx                               context.Context
	cancel                            context.CancelFunc
	closeOnce                         sync.Once
}

func New(endpoint, token, operation string, warnings io.Writer) (*Reporter, error) {
	parsed, err := url.Parse(endpoint)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.ForceQuery || strings.Contains(endpoint, "#") || parsed.Opaque != "" {
		return nil, errors.New("--progress-url must be an explicit http/https URL without credentials, query or fragment")
	}
	if token == "" || len(token) > 4096 {
		return nil, errors.New("--progress-token must be a bounded visible ASCII token")
	}
	for _, char := range token {
		if char < 33 || char > 126 {
			return nil, errors.New("--progress-token must be a bounded visible ASCII token")
		}
	}
	if operation != "install" && operation != "status" && operation != "bootstrap" {
		return nil, errors.New("invalid installation progress operation")
	}
	var id [16]byte
	if _, err = rand.Read(id[:]); err != nil {
		return nil, errors.New("could not initialize installation progress reporting")
	}
	ctx, cancel := context.WithCancel(context.Background())
	if warnings == nil {
		warnings = io.Discard
	}
	reporter := &Reporter{endpoint: parsed.String(), token: token, operation: operation, runID: hex.EncodeToString(id[:]), client: &http.Client{Timeout: 2 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, warnings: warnings, wake: make(chan struct{}, 1), done: make(chan struct{}), changed: make(chan struct{}), ctx: ctx, cancel: cancel}
	go reporter.run()
	return reporter, nil
}

func (r *Reporter) notify() {
	select {
	case r.wake <- struct{}{}:
	default:
	}
}
func (r *Reporter) warn() { r.warnOnce.Do(func() { fmt.Fprint(r.warnings, warning) }) }

// Observe accepts Manager.Progress's already allowlisted image fields. No error
// strings, configuration values or raw process output enter the reporting body.
func (r *Reporter) Observe(progress sandboxplugin.ProgressEvent) {
	if r == nil {
		return
	}
	next := event{Stage: progress.Stage, State: progress.State, Detail: progress.Detail, ElapsedMS: progress.Elapsed.Milliseconds()}
	if next.ElapsedMS < 0 {
		next.ElapsedMS = 0
	}
	allowed := false
	if r.operation == "bootstrap" {
		switch next.Stage {
		case "host", "images", "configuration", "gateway", "plugin", "readiness", "registration":
			allowed = true
		}
	} else {
		switch next.Stage {
		case "configuration", "docker", "image", "state", "container", "readiness":
			allowed = true
		}
	}
	if !allowed {
		return
	}
	switch next.State {
	case "start", "wait", "done", "failed":
	default:
		return
	}
	switch next.Detail {
	case "", "cached", "pulling", "pulled", "ready", "not_ready", "timeout", "canceled", "not_installed":
	default:
		return
	}
	if r.operation == "bootstrap" && progress.Diagnostic.Valid() && (next.State == "failed" || (next.Stage == "readiness" && next.State == "done" && next.Detail == "not_ready" && progress.Diagnostic.Code == "provider_not_ready")) {
		copied := *progress.Diagnostic
		next.Diagnostic = &copied
	}
	if progress.Image != nil && (next.Stage == "image" || next.Stage == "images") && next.State == "wait" {
		next.Image = &image{Layer: progress.Image.Layer, Status: progress.Image.Status, Current: progress.Image.Current, Total: progress.Image.Total}
	}
	r.mu.Lock()
	if r.closed || r.failed {
		r.mu.Unlock()
		return
	}
	if next.State == "wait" {
		// Remove a superseded pending update and append its latest value, retaining
		// chronological elapsed times across updates from different Docker layers.
		for i := len(r.pending) - 1; i >= 0; i-- {
			prior := r.pending[i]
			if prior.State != "wait" || prior.Stage != next.Stage {
				break
			}
			sameLayer := (prior.Image == nil && next.Image == nil) || (prior.Image != nil && next.Image != nil && prior.Image.Layer == next.Image.Layer)
			if sameLayer {
				r.pending = append(r.pending[:i], r.pending[i+1:]...)
				break
			}
		}
	}
	if len(r.pending) >= maxQueued {
		dropped := false
		for i, prior := range r.pending {
			if prior.State == "wait" {
				r.pending = append(r.pending[:i], r.pending[i+1:]...)
				dropped = true
				break
			}
		}
		if !dropped {
			// Real installation/status streams have at most twelve phase edges. A
			// malformed internal stream must not grow memory or block the manager.
			if next.State != "wait" {
				r.failed = true
			}
			r.mu.Unlock()
			r.notify()
			return
		}
	}
	r.pending = append(r.pending, next)
	r.mu.Unlock()
	r.notify()
}

// Close permits a bounded final flush even when the installation context was
// canceled. Its private context is canceled at the four-second deadline.
func (r *Reporter) Close() {
	if r == nil {
		return
	}
	r.closeOnce.Do(func() {
		r.mu.Lock()
		r.closed = true
		r.mu.Unlock()
		r.notify()
		timer := time.NewTimer(4 * time.Second)
		defer timer.Stop()
		select {
		case <-r.done:
		case <-timer.C:
			r.cancel()
			<-r.done
		}
		r.cancel()
	})
}

func (r *Reporter) run() {
	defer func() {
		r.mu.Lock()
		r.failed = r.failed || r.inFlight || len(r.pending) > 0
		r.inFlight = false
		r.signalChange()
		r.mu.Unlock()
		close(r.done)
	}()
	var assigned int64
	for {
		r.mu.Lock()
		failed, closed := r.failed, r.closed
		count := len(r.pending)
		if count > 32 {
			count = 32
		}
		events := append([]event(nil), r.pending[:count]...)
		r.pending = r.pending[count:]
		r.inFlight = count > 0
		r.mu.Unlock()
		if failed {
			r.warn()
			return
		}
		if len(events) == 0 {
			if closed {
				return
			}
			select {
			case <-r.ctx.Done():
				r.warn()
				return
			case <-r.wake:
				continue
			}
		}
		for i := range events {
			assigned++
			events[i].Seq = assigned
		}
		for len(events) > 0 {
			payload, err := json.Marshal(batch{Operation: r.operation, RunID: r.runID, Events: events})
			if err != nil || len(payload) > 16*1024 {
				r.warn()
				return
			}
			ack, retry := r.post(payload, events[0].Seq-1, events[len(events)-1].Seq)
			if ack >= events[0].Seq {
				offset := int(ack - events[0].Seq + 1)
				events = events[offset:]
				if len(events) == 0 {
					r.mu.Lock()
					r.inFlight = false
					r.signalChange()
					r.mu.Unlock()
					break
				}
			}
			if !retry {
				r.warn()
				return
			}
			r.warn()
			timer := time.NewTimer(100 * time.Millisecond)
			select {
			case <-r.ctx.Done():
				timer.Stop()
				return
			case <-timer.C:
			}
		}
	}
}

// post never reads error bodies into diagnostics. A lost acknowledgement is
// retried verbatim; the server's last_seq makes replay idempotent.
func (r *Reporter) post(payload []byte, prior, last int64) (int64, bool) {
	req, err := http.NewRequestWithContext(r.ctx, http.MethodPost, r.endpoint, bytes.NewReader(payload))
	if err != nil {
		return prior, false
	}
	req.Header.Set("Authorization", "Bearer "+r.token)
	req.Header.Set("Content-Type", "application/json")
	response, err := r.client.Do(req)
	if err != nil {
		return prior, true
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return prior, response.StatusCode == 408 || response.StatusCode == 429 || response.StatusCode >= 500
	}
	raw, err := io.ReadAll(io.LimitReader(response.Body, 16*1024+1))
	if err != nil || len(raw) > 16*1024 {
		return prior, true
	}
	var result struct {
		Code *int `json:"code"`
		Data struct {
			LastSeq *int64 `json:"last_seq"`
		} `json:"data"`
	}
	if json.Unmarshal(raw, &result) != nil || result.Code == nil || *result.Code != 0 || result.Data.LastSeq == nil || *result.Data.LastSeq < prior || *result.Data.LastSeq > last {
		return prior, true
	}
	return *result.Data.LastSeq, true
}

// RunID binds claim, progress, and enrollment to this CLI invocation.
func (r *Reporter) RunID() string { return r.runID }

// signalChange is called only while mu is held.
func (r *Reporter) signalChange() { close(r.changed); r.changed = make(chan struct{}) }

// Flush waits for all currently queued progress to be acknowledged without
// closing the stream. Bootstrap must acknowledge readiness before enrollment.
func (r *Reporter) Flush(ctx context.Context) error {
	if r == nil {
		return errors.New("installation progress is unavailable")
	}
	for {
		r.mu.Lock()
		failed, empty, changed := r.failed, len(r.pending) == 0 && !r.inFlight, r.changed
		r.mu.Unlock()
		if failed {
			return errors.New("installation progress could not be synchronized")
		}
		if empty {
			return nil
		}
		select {
		case <-ctx.Done():
			return errors.New("installation progress could not be synchronized")
		case <-changed:
		case <-r.done:
		}
	}
}
