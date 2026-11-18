package sandboxbootstrap

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/installprogress"
	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
)

type progress struct {
	mu       sync.Mutex
	stage    string
	active   bool
	started  time.Time
	reporter *installprogress.Reporter
	out      io.Writer
	stop     chan struct{}
	done     chan struct{}
}

var titles = map[string]string{"host": "Checking this AI Vision deployment host", "images": "Preparing pinned OpenShell images", "configuration": "Preparing private configuration and credentials", "gateway": "Starting the dedicated authenticated gateway", "plugin": "Starting the owned OpenShell service", "readiness": "Checking actual provider readiness", "registration": "Saving and testing the Space connection"}
var stageNumbers = map[string]int{"host": 1, "images": 2, "configuration": 3, "gateway": 4, "plugin": 5, "readiness": 6, "registration": 7}

func newProgress(reporter *installprogress.Reporter, out io.Writer) *progress {
	p := &progress{started: time.Now(), reporter: reporter, out: out, stop: make(chan struct{}), done: make(chan struct{})}
	go func() {
		defer close(p.done)
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-p.stop:
				return
			case <-ticker.C:
				p.wait("", nil)
			}
		}
	}()
	return p
}
func (p *progress) emit(state, detail string, image *sandboxplugin.ImageProgress) {
	p.reporter.Observe(sandboxplugin.ProgressEvent{Stage: p.stage, State: state, Detail: detail, Elapsed: time.Since(p.started), Image: image})
	if state == "start" {
		fmt.Fprintf(p.out, "[%d/7] %s...\n", stageNumbers[p.stage], titles[p.stage])
	} else if state == "done" || state == "failed" {
		fmt.Fprintf(p.out, "[%d/7] %s: %s (%s elapsed).\n", stageNumbers[p.stage], titles[p.stage], state, time.Since(p.started).Round(time.Second))
	} else if image != nil {
		fmt.Fprintf(p.out, "[2/7] Layer %s: %s %s / %s.\n", image.Layer, image.Status, image.Current, image.Total)
	} else {
		fmt.Fprintf(p.out, "[%d/7] %s: waiting (%s elapsed).\n", stageNumbers[p.stage], titles[p.stage], time.Since(p.started).Round(time.Second))
	}
}
func (p *progress) start(stage string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.stage = stage
	p.active = true
	p.emit("start", "", nil)
}
func (p *progress) wait(detail string, image *sandboxplugin.ImageProgress) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.active {
		p.emit("wait", detail, image)
	}
}
func (p *progress) finish(state, detail string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.active {
		p.emit(state, detail, nil)
		p.active = false
	}
}
func (p *progress) notReady() {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.active {
		p.reporter.Observe(sandboxplugin.ProgressEvent{Stage: p.stage, State: "done", Detail: "not_ready", Elapsed: time.Since(p.started), Diagnostic: &sandboxplugin.InstallationDiagnostic{Code: "provider_not_ready"}})
		fmt.Fprintf(p.out, "[%d/7] %s: not ready (%s elapsed).\n", stageNumbers[p.stage], titles[p.stage], time.Since(p.started).Round(time.Second))
		p.active = false
	}
}
func (p *progress) fail(diagnostic *sandboxplugin.InstallationDiagnostic) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.active {
		detail := ""
		if diagnostic.Code == "timeout" || diagnostic.Code == "canceled" {
			detail = diagnostic.Code
		}
		p.reporter.Observe(sandboxplugin.ProgressEvent{Stage: p.stage, State: "failed", Detail: detail, Elapsed: time.Since(p.started), Diagnostic: diagnostic})
		fmt.Fprintf(p.out, "[%d/7] %s: failed (%s elapsed).\n", stageNumbers[p.stage], titles[p.stage], time.Since(p.started).Round(time.Second))
		p.active = false
	}
}
func (p *progress) close() { close(p.stop); <-p.done }

func verifyImage(ctx context.Context, docker Docker, ref, platform string) error {
	raw, err := docker.Run(ctx, "image", "inspect", "--format", "{{json .}}", ref)
	var image struct {
		ID, OS, Architecture string
		RepoDigests          []string
	}
	if err != nil || len(raw) > 1024*1024 || json.Unmarshal(raw, &image) != nil {
		return errors.New("a required pinned image is unavailable")
	}
	matched := false
	for _, digest := range image.RepoDigests {
		matched = matched || digest == ref
	}
	if !matched || image.OS+"/"+image.Architecture != platform || !strings.HasPrefix(image.ID, "sha256:") || !fullID.MatchString(strings.TrimPrefix(image.ID, "sha256:")) {
		return errors.New("a pinned image does not match the actual Docker daemon platform or digest")
	}
	return nil
}
func acquireImages(ctx context.Context, docker Docker, images Images, platform string, p *progress) (bool, error) {
	cached := true
	seen := map[string]bool{}
	for _, ref := range []string{images.Plugin, images.Worker, images.Gateway, images.Runtime, images.Supervisor} {
		if seen[ref] {
			continue
		}
		seen[ref] = true
		if err := verifyImage(ctx, docker, ref, platform); err == nil {
			continue
		}
		cached = false
		p.wait("pulling", nil)
		if err := docker.Pull(ctx, ref, func(image sandboxplugin.ImageProgress) { p.wait("pulling", &image) }); err != nil {
			return false, errors.New("could not acquire the exact pinned OpenShell images; no alternate images were substituted")
		}
		if err := verifyImage(ctx, docker, ref, platform); err != nil {
			return false, err
		}
	}
	return cached, nil
}
func probeSocketGroup(ctx context.Context, docker Docker, plan Plan, state *ownedState, images Images, runID string) (int, error) {
	raw, err := docker.Run(ctx, "run", "--rm", "--name", plan.Target.ServiceName+"-socket-"+runID[:8], "--network", "none", "--user", fmt.Sprintf("%d:%d", os.Getuid(), os.Getgid()), "--security-opt", "no-new-privileges=true", "--cap-drop", "ALL", "--mount", "type=bind,source="+state.Host.Socket+",target=/run/docker-readback.sock,readonly", "--entrypoint", "/usr/bin/stat", images.Plugin, "-c", "%g", "/run/docker-readback.sock")
	gid, parseErr := strconv.Atoi(strings.TrimSpace(string(raw)))
	if err != nil || len(raw) > 32 || parseErr != nil || gid < 0 || gid > 2147483647 {
		return 0, errors.New("could not determine the mounted Docker socket group with the trusted metadata probe")
	}
	return gid, nil
}
func pluginOptions(plan Plan, state *ownedState, images Images) sandboxplugin.Options {
	tlsDir := filepath.Join(state.Dir, "gateway-state/tls")
	gid := state.SocketGID
	return sandboxplugin.Options{Image: images.Plugin, Gateway: plan.Target.GatewayName + ":8080", WorkloadImage: images.Worker, TokenFile: filepath.Join(state.Dir, "plugin-token"), PolicyFile: filepath.Join(state.Dir, "policy.json"), DockerSocket: state.Host.Socket, JournalDir: filepath.Join(state.Dir, "journal"), GatewayCAFile: filepath.Join(tlsDir, "ca.crt"), GatewayCertFile: filepath.Join(tlsDir, "client/tls.crt"), GatewayKeyFile: filepath.Join(tlsDir, "client/tls.key"), Workspace: "default", Port: state.PluginPort, SocketGIDOverride: &gid, Network: state.Host.NetworkID, NetworkAlias: plan.Target.ServiceName}
}
func ownedPlugin(ctx context.Context, manager *sandboxplugin.Manager, options sandboxplugin.Options) (*sandboxplugin.Status, error) {
	saved := filepath.Join(manager.StateDir, "installation.json")
	raw, err := privateFile(saved)
	if err != nil {
		if !os.IsNotExist(err) {
			return nil, errors.New("saved plugin state is unsafe")
		}
		return manager.Install(ctx, options)
	}
	var pending struct {
		ContainerID string `json:"container_id"`
	}
	if json.Unmarshal(raw, &pending) != nil {
		return nil, errors.New("saved plugin state is invalid")
	}
	status, err := manager.Status(ctx)
	if err == nil && status != nil && status.Installed {
		return status, nil
	}
	if pending.ContainerID != "" {
		return status, errors.New("the existing owned plugin cannot be verified; inspect its retained state")
	}
	// Manager checks every original option and its journal/name fences before
	// resuming a pending installation; it never adopts an unknown container.
	return manager.Install(ctx, options)
}

// Setup executes only the immutable plan claimed for this process. Host
// identity checks precede resource changes; registration follows a real-ready
// status and an acknowledged progress barrier.
func Setup(ctx context.Context, client *Client, reporter *installprogress.Reporter, docker Docker, root string, out io.Writer) (err error) {
	plan, err := client.Claim(ctx, reporter.RunID())
	if err != nil {
		return err
	}
	p := newProgress(reporter, out)
	defer p.close()
	var owned *ownedState
	defer func() {
		if err != nil {
			p.fail(failureDiagnostic(err, p.stage, ctx.Err()))
			if owned != nil {
				err = fmt.Errorf("%w; retained setup: %s", err, owned.Dir)
			}
		}
	}()
	p.start("host")
	host, err := Discover(ctx, docker, plan.Target)
	if err != nil {
		return err
	}
	deferred, err := checkDiscoveredEnvironment(ctx, docker, host)
	if err != nil {
		return err
	}
	if deferred {
		fmt.Fprintln(out, "Docker runs in a different kernel; Landlock qualification is deferred to the installed plugin before connection registration.")
	}
	images, supported := plan.Catalog.Images[host.Platform]
	if !supported {
		return errors.New("the installed product catalog has no qualified artifacts for this Docker daemon platform")
	}
	owned, unlock, err := openOwned(ctx, root, plan, host)
	if err != nil {
		return err
	}
	defer unlock()
	p.finish("done", "")
	p.start("images")
	cached, err := acquireImages(ctx, docker, images, host.Platform, p)
	if err != nil {
		return err
	}
	detail := "pulled"
	if cached {
		detail = "cached"
	}
	p.finish("done", detail)
	p.start("configuration")
	if err = owned.configure(); err != nil {
		return err
	}
	owned.SocketGID, err = probeSocketGroup(ctx, docker, plan, owned, images, reporter.RunID())
	if err != nil {
		return err
	}
	if err = owned.save(); err != nil {
		return err
	}
	if err = createFixed(filepath.Join(owned.Dir, "gateway.toml"), owned.gatewayConfig(plan, images), false); err != nil {
		return err
	}
	p.finish("done", "")
	p.start("gateway")
	if err = ensureTLS(ctx, docker, plan, owned, images); err != nil {
		return err
	}
	if err = startGateway(ctx, docker, plan, owned, images); err != nil {
		return err
	}
	p.finish("done", "")
	p.start("plugin")
	manager := sandboxplugin.New(filepath.Join(owned.Dir, "installation"))
	manager.Docker = docker
	manager.Progress = func(event sandboxplugin.ProgressEvent) { p.wait("", nil) }
	if _, err = ownedPlugin(ctx, manager, pluginOptions(plan, owned, images)); err != nil {
		return err
	}
	p.finish("done", "")
	p.start("readiness")
	status, err := manager.Status(ctx)
	if err != nil {
		return errors.New("installed service readiness could not be verified")
	}
	return finishRegistration(ctx, client, reporter, p, plan, host, owned, status, out)
}

// finishRegistration admits only a ready, environment-qualified provider.
func finishRegistration(ctx context.Context, client *Client, reporter *installprogress.Reporter, p *progress, plan Plan, host Host, owned *ownedState, status *sandboxplugin.Status, out io.Writer) error {
	var err error
	// Preserve a measured environment failure even when provider readiness also fails.
	if status != nil && status.Health != nil && status.Health.Environment != nil {
		if err = runtimeEnvironmentError(status.Health); err != nil {
			return err
		}
	}
	if status == nil || !status.Installed || status.Health == nil || status.Health.Ready == nil || !*status.Health.Ready {
		p.notReady()
		return errors.New("the service is installed but the provider is not ready; inspect status before preparing a new setup session")
	}
	if status.Health.Environment == nil {
		return runtimeEnvironmentError(status.Health)
	}
	p.finish("done", "ready")
	p.start("registration")
	flush, cancel := context.WithTimeout(ctx, 4*time.Second)
	err = reporter.Flush(flush)
	cancel()
	if err != nil {
		return errors.New("service is ready but progress could not synchronize; prepare a new setup session to retry registration with the same owned state")
	}
	key, err := privateFile(filepath.Join(owned.Dir, "plugin-token"))
	if err != nil {
		return errors.New("generated service credential is unavailable")
	}
	if err = client.Enroll(ctx, reporter.RunID(), plan, host.Platform, string(key)); err != nil {
		return err
	}
	p.finish("done", "ready")
	fmt.Fprintln(out, "OpenShell service is ready and the Space connection was saved. Set it as the Space default explicitly in AI Vision.")
	fmt.Fprintf(out, "Owned state: %s\n", owned.Dir)
	return nil
}
