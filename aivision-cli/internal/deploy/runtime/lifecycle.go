package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
)

type containerLifecycleState struct {
	ID         string
	Status     string
	Running    bool
	Restarting bool
	Dead       bool
	ExitCode   int
	OOMKilled  bool
}

// Only the explicit image-product blueprint opts into application shutdown
// contracts. Source processes and custom blueprints retain their old behavior.
func (a *App) productContainerStop(c Component) (time.Duration, string, bool) {
	if a.BP.Metadata.Product != "aivision" || c.Kind != "container" {
		return 0, "", false
	}
	switch components.Canonical(c.Name) {
	case "frontend":
		return 30 * time.Second, "SIGQUIT", true
	case "diagnosis":
		return 300 * time.Second, "SIGTERM", true
	case "backend", "ingest-gateway", "data-backend":
		return 60 * time.Second, "SIGTERM", true
	default:
		return 0, "", false
	}
}

func (a *App) inspectLifecycleState(ctx context.Context, name string) (containerLifecycleState, error) {
	var snapshot struct {
		ID    string
		State containerLifecycleState
	}
	out, err := a.Docker.Run(ctx, true, "inspect", "--format", `{"ID":{{json .Id}},"State":{{json .State}}}`, name)
	if err != nil {
		return snapshot.State, err
	}
	if err := json.Unmarshal([]byte(out), &snapshot); err != nil {
		return snapshot.State, fmt.Errorf("read container %s state: %w", name, err)
	}
	state := snapshot.State
	state.ID = snapshot.ID
	if state.ID == "" {
		return state, fmt.Errorf("container %s has no identity", name)
	}
	return state, nil
}

func (a *App) stopRecord(c Component) string {
	return filepath.Join(a.State.StateDir, "lifecycle-stops", components.Canonical(c.Name)+".json")
}

func (a *App) productContainerExists(ctx context.Context, name string) (bool, error) {
	if _, err := a.Docker.Run(ctx, true, "inspect", "--format", "{{.Id}}", name); err == nil {
		return true, nil
	}
	// Failed inspect can mean an unavailable daemon. Only a successful listing
	// can establish absence and permit clearing a previous stop-failure gate.
	out, err := a.Docker.Run(ctx, true, "container", "ls", "--all", "--format", "{{.Names}}", "--filter", "name="+name)
	if err != nil {
		return false, fmt.Errorf("verify container %s presence: %w", name, err)
	}
	for _, listed := range strings.Fields(out) {
		if listed == name {
			return false, fmt.Errorf("container %s exists but could not be inspected", name)
		}
	}
	return false, nil
}

func (a *App) recordProductStop(c Component, id string) error {
	path := a.stopRecord(c)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	data, err := json.Marshal(map[string]string{"component": components.Canonical(c.Name), "container_id": id})
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".stop-")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if _, err := file.Write(data); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(file.Name(), path)
}

func (a *App) pendingProductStop(c Component, id string) (bool, error) {
	data, err := os.ReadFile(a.stopRecord(c))
	if os.IsNotExist(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	var record struct {
		ID string `json:"container_id"`
	}
	if err := json.Unmarshal(data, &record); err != nil || record.ID == "" {
		return false, fmt.Errorf("invalid pending stop record for %s; inspect %s", components.Canonical(c.Name), a.stopRecord(c))
	}
	if record.ID != id {
		// An operator removed/replaced the failed container; never apply its
		// stop result to another container that reused the physical name.
		return false, os.Remove(a.stopRecord(c))
	}
	return true, nil
}

// Only the historical image entrypoint runs Django directly as PID 1. That
// command ignores TERM at PID 1 but exits cleanly on INT. Current backend
// supervisors must keep TERM so their Gunicorn workers drain gracefully.
func legacyDjangoBackendCommand(command string) bool {
	if !strings.HasSuffix(command, "\x00") {
		return false
	}
	argv := strings.Split(strings.TrimSuffix(command, "\x00"), "\x00")
	if len(argv) != 5 || (argv[1] != "manage.py" && argv[1] != "/app/manage.py") ||
		argv[2] != "runserver" || argv[4] != "--noreload" {
		return false
	}
	switch filepath.Base(argv[0]) {
	case "python", "python3", "python3.11":
	default:
		return false
	}
	host, port, err := net.SplitHostPort(argv[3])
	if err != nil || host != "0.0.0.0" {
		return false
	}
	number, err := strconv.ParseUint(port, 10, 16)
	return err == nil && number != 0
}

func (a *App) stopProductContainer(c Component, strictStopped bool) error {
	budget, signal, enabled := a.productContainerStop(c)
	if !enabled {
		return nil
	}
	name := a.ContainerName(c)
	exists, err := a.productContainerExists(a.Ctx, name)
	if err != nil {
		return err
	}
	if !exists {
		if err := os.Remove(a.stopRecord(c)); err != nil && !os.IsNotExist(err) {
			return err
		}
		return nil
	}
	ctx, cancel := context.WithTimeout(a.Ctx, budget)
	defer cancel()
	state, err := a.inspectLifecycleState(ctx, name)
	if err != nil {
		return err
	}
	if state.Restarting || state.Dead {
		return fmt.Errorf("%s is restarting or dead; inspect its state before replacement", components.Canonical(c.Name))
	}
	pending, err := a.pendingProductStop(c, state.ID)
	if err != nil {
		return err
	}
	// A cast may replace an already-failed container after the operator fixes
	// its configuration. Destroy is strict even for previously stopped failures
	// so repeating destroy cannot bypass an earlier failed stop before purge.
	if !state.Running && !strictStopped && !pending {
		return nil
	}
	if state.Running {
		if err := a.recordProductStop(c, state.ID); err != nil {
			return fmt.Errorf("record %s stop before sending a signal: %w", components.Canonical(c.Name), err)
		}
		id := state.ID
		if components.Canonical(c.Name) == "backend" {
			command, err := a.Docker.Run(ctx, true, "exec", id, "cat", "/proc/1/cmdline")
			// An unavailable or unrecognized command never guesses a legacy
			// signal: preserve TERM and the existing timeout/exit-code gates.
			if err == nil && legacyDjangoBackendCommand(command) {
				signal = "SIGINT"
			}
		}
		a.Log.Info("stopping %s with %s (up to %s)", components.Canonical(c.Name), signal, budget)
		if _, err := a.Docker.Run(ctx, false, "kill", "--signal", signal, id); err != nil {
			return fmt.Errorf("request %s shutdown: %w", components.Canonical(c.Name), err)
		}
		for {
			state, err = a.inspectLifecycleState(ctx, id)
			if err != nil {
				return fmt.Errorf("wait for %s shutdown; container retained: %w", components.Canonical(c.Name), err)
			}
			if !state.Running {
				break
			}
			select {
			case <-ctx.Done():
				return fmt.Errorf("%s did not stop within %s; container retained: %w", components.Canonical(c.Name), budget, ctx.Err())
			case <-time.After(time.Second):
			}
		}
	}
	if state.Status != "exited" || state.Running || state.Restarting || state.Dead || state.ExitCode != 0 || state.OOMKilled {
		return fmt.Errorf("%s stopped with status=%s exit=%d oomKilled=%t; container retained for inspection; restart and stop it successfully or explicitly remove the failed container before retrying",
			components.Canonical(c.Name), state.Status, state.ExitCode, state.OOMKilled)
	}
	if err := os.Remove(a.stopRecord(c)); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func (a *App) stopProductConsumers() error {
	if a.BP.Metadata.Product != "aivision" {
		return nil
	}
	ordered := a.BP.OrderedComponents()
	for i := len(ordered) - 1; i >= 0; i-- {
		if err := a.stopProductContainer(ordered[i], false); err != nil {
			return err
		}
	}
	return nil
}
