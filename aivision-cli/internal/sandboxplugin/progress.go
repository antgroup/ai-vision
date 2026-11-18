package sandboxplugin

import (
	"regexp"
	"sync"
	"time"
)

// ProgressEvent describes a trusted installation phase. It never carries raw
// Docker output, operator file contents, or provider response bodies.
type InstallationDiagnostic struct {
	Code     string `json:"code"`
	Actual   string `json:"actual,omitempty"`
	Required string `json:"required,omitempty"`
}

var diagnosticNumber = regexp.MustCompile(`^[0-9]+(?:\.[0-9]+)*$`)

func (d *InstallationDiagnostic) Valid() bool {
	if d == nil {
		return false
	}
	switch d.Code {
	case "docker_version_unsupported", "landlock_unavailable", "landlock_abi_unsupported", "environment_probe_failed", "image_download_failed", "gateway_start_failed", "plugin_start_failed", "provider_not_ready", "registration_failed", "configuration_failed", "timeout", "canceled", "docker_unavailable", "docker_permission_denied", "docker_context_unsupported", "deployment_not_found", "deployment_ambiguous":
	default:
		return false
	}
	for _, value := range []string{d.Actual, d.Required} {
		if value != "" && (len(value) > 64 || !diagnosticNumber.MatchString(value)) {
			return false
		}
	}
	return true
}

type EnvironmentHealth struct {
	Status        string                  `json:"status"`
	DockerVersion string                  `json:"docker_version"`
	LandlockABI   *int                    `json:"landlock_abi"`
	Diagnostic    *InstallationDiagnostic `json:"diagnostic,omitempty"`
}

type ProgressEvent struct {
	Stage      string
	State      string
	Detail     string
	Elapsed    time.Duration
	Image      *ImageProgress
	Diagnostic *InstallationDiagnostic
}

// installProgress owns at most one phase ticker. A phase is fully stopped before
// its final event, so observers never see a late heartbeat after completion.
type installProgress struct {
	notify func(ProgressEvent)
	phase  *installPhase
	stage  string
}

type installPhase struct {
	stage   string
	started time.Time
	notify  func(ProgressEvent)
	mu      sync.Mutex
	stopped bool
	stop    chan struct{}
	done    chan struct{}
}

func (p *installProgress) start(stage string) {
	p.stage = stage
	phase := &installPhase{stage: stage, started: time.Now(), notify: p.notify, stop: make(chan struct{}), done: make(chan struct{})}
	p.phase = phase
	phase.emit("start", "", nil)
	if p.notify == nil {
		close(phase.done)
		return
	}
	go func() {
		defer close(phase.done)
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-phase.stop:
				return
			case <-ticker.C:
				phase.emit("wait", "", nil)
			}
		}
	}()
}

func (p *installProgress) update(detail string, image *ImageProgress) {
	p.phase.emit("wait", detail, image)
}

func (p *installProgress) finish(state, detail string) {
	if p.phase == nil {
		return
	}
	phase := p.phase
	close(phase.stop)
	<-phase.done
	phase.mu.Lock()
	phase.stopped = true
	if phase.notify != nil {
		phase.notify(ProgressEvent{Stage: phase.stage, State: state, Detail: detail, Elapsed: time.Since(phase.started)})
	}
	phase.mu.Unlock()
	p.phase = nil
}

func (p *installPhase) emit(state, detail string, image *ImageProgress) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if !p.stopped && p.notify != nil {
		p.notify(ProgressEvent{Stage: p.stage, State: state, Detail: detail, Elapsed: time.Since(p.started), Image: image})
	}
}
