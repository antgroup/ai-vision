package runtime

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const oceanbaseLifecycleLabel = "aivision.forge.database-lifecycle"

func (a *App) managedOceanbase(c Component) bool {
	return c.Name == "oceanbase" && a.Vars["OB_BACKEND"] == "oceanbase" && a.OBManaged()
}

func (a *App) managedDatastore(c Component) bool {
	return c.Name == "oceanbase" && a.OBManaged() &&
		(a.Vars["OB_BACKEND"] == "oceanbase" || a.Vars["OB_BACKEND"] == "seekdb")
}

// The seekdb preStart shell and Go container replacement share this private,
// single-ID stop gate. Keep it separate from application JSON stop records so
// the shell does not need a JSON parser on Docker-only deployment hosts.
func (a *App) databaseStopRecord() string {
	return filepath.Join(a.State.StateDir, "lifecycle-stops", "database-id")
}

func (a *App) clearDatabaseStop() error {
	err := os.Remove(a.databaseStopRecord())
	if os.IsNotExist(err) {
		return nil
	}
	return err
}

func validDatabaseContainerID(id string) bool {
	if id == "" {
		return false
	}
	for _, character := range id {
		if !((character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') ||
			(character >= '0' && character <= '9') || character == '-' || character == '_') {
			return false
		}
	}
	return true
}

func (a *App) pendingDatabaseStop(id string) (bool, error) {
	data, err := os.ReadFile(a.databaseStopRecord())
	if os.IsNotExist(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	previous := string(data)
	if !validDatabaseContainerID(previous) {
		return false, fmt.Errorf("invalid pending database stop record; inspect %s", a.databaseStopRecord())
	}
	if previous != id {
		return false, a.clearDatabaseStop()
	}
	return true, nil
}

func (a *App) recordDatabaseStop(id string) error {
	if !validDatabaseContainerID(id) {
		return fmt.Errorf("cannot record database stop without a valid container identity")
	}
	path := a.databaseStopRecord()
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".database-stop-")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if _, err := file.WriteString(id); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(file.Name(), path)
}

func databaseStoppedCleanly(state containerLifecycleState) bool {
	return state.Status == "exited" && !state.Running && !state.Restarting && !state.Dead && state.ExitCode == 0 && !state.OOMKilled
}

// The old CLI used rm -f. Stop the database before replacing its container,
// including legacy containers whose upstream PID 1 does not handle TERM.
func (a *App) removeContainer(c Component) error {
	return a.removeContainerWithChecks(c, false)
}

func (a *App) removeContainerWithChecks(c Component, strictStopped bool) error {
	if err := a.stopProductContainer(c, strictStopped); err != nil {
		return err
	}
	name := a.ContainerName(c)
	if !a.managedDatastore(c) {
		return a.Docker.RemoveContainer(a.Ctx, name)
	}
	exists, err := a.productContainerExists(a.Ctx, name)
	if err != nil {
		return err
	}
	if !exists {
		if err := a.clearDatabaseStop(); err != nil {
			return err
		}
		return nil
	}
	state, err := a.inspectLifecycleState(a.Ctx, name)
	if err != nil {
		return err
	}
	pending, err := a.pendingDatabaseStop(state.ID)
	if err != nil {
		return err
	}
	if !state.Running && pending && !databaseStoppedCleanly(state) {
		return fmt.Errorf("database stopped with status=%s exit=%d oomKilled=%t; container retained for inspection; restart and stop it successfully or explicitly remove it before retrying", state.Status, state.ExitCode, state.OOMKilled)
	}
	if state.Running {
		if err := a.recordDatabaseStop(state.ID); err != nil {
			return fmt.Errorf("record database stop before sending a signal: %w", err)
		}
		lifecycle, err := a.Docker.Run(a.Ctx, true, "inspect", "--format", `{{index .Config.Labels "`+oceanbaseLifecycleLabel+`"}}`, name)
		if err != nil {
			return err
		}
		if strings.TrimSpace(lifecycle) == "ce-v1" || a.Vars["OB_BACKEND"] == "seekdb" {
			nativeSeekdb := false
			if a.Vars["OB_BACKEND"] == "seekdb" {
				entrypoint, err := a.Docker.Run(a.Ctx, true, "inspect", "--format", "{{json .Config.Entrypoint}}", name)
				if err != nil {
					return err
				}
				nativeSeekdb = strings.TrimSpace(entrypoint) == `["/usr/bin/seekdb"]`
			}
			if nativeSeekdb {
				if err := a.stopNativeSeekdb(name); err != nil {
					return err
				}
			} else {
				if _, err := a.Docker.Run(a.Ctx, false, "stop", "--time", "120", name); err != nil {
					return fmt.Errorf("stop database container: %w", err)
				}
			}
			state, err = a.inspectLifecycleState(a.Ctx, state.ID)
			if err != nil {
				return fmt.Errorf("inspect stopped database: %w", err)
			}
			if !databaseStoppedCleanly(state) {
				return fmt.Errorf("database did not stop cleanly (status=%s exit=%d oomKilled=%t); container retained for inspection", state.Status, state.ExitCode, state.OOMKilled)
			}
		} else {
			// The legacy entrypoint never exits on TERM. Stop its engine through
			// OBD first; only its idle bootstrap shell is force-removed afterward.
			if _, err := a.Docker.Run(a.Ctx, false, "exec", name, "/bin/sh", "-ec", oceanbaseLegacyStop); err != nil {
				return fmt.Errorf("stop legacy database before replacement: %w", err)
			}
		}
	}
	if strictStopped && a.BP.Metadata.Product == "aivision" {
		code, err := a.Docker.Run(a.Ctx, true, "inspect", "--format", "{{.State.ExitCode}}", name)
		if err != nil || strings.TrimSpace(code) != "0" {
			return fmt.Errorf("database exit %s is not successful; refusing removal or data purge", strings.TrimSpace(code))
		}
		oom, err := a.Docker.Run(a.Ctx, true, "inspect", "--format", "{{.State.OOMKilled}}", name)
		if err != nil || strings.TrimSpace(oom) == "true" {
			return fmt.Errorf("database shutdown cannot be verified as free of OOM; refusing removal or data purge")
		}
	}
	if err := a.clearDatabaseStop(); err != nil {
		return err
	}
	return a.Docker.RemoveContainer(a.Ctx, name)
}

// Older CLI containers run seekdb as PID 1. Its supported shutdown signal is
// USR1; TERM requests an immediate kill in the pinned engine. docker kill plus
// polling also works with Docker clients predating `stop --signal`.
func (a *App) stopNativeSeekdb(name string) error {
	ctx, cancel := context.WithTimeout(a.Ctx, 120*time.Second)
	defer cancel()
	if _, err := a.Docker.Run(ctx, false, "kill", "--signal", "SIGUSR1", name); err != nil {
		return fmt.Errorf("request legacy seekdb shutdown: %w", err)
	}
	for {
		running, err := a.Docker.Run(ctx, true, "inspect", "--format", "{{.State.Running}}", name)
		if err != nil {
			return fmt.Errorf("wait for legacy seekdb shutdown (container retained): %w", err)
		}
		if strings.TrimSpace(running) == "false" {
			return nil
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("legacy seekdb did not stop within 120s; container retained: %w", ctx.Err())
		case <-time.After(time.Second):
		}
	}
}

const oceanbaseLegacyStop = `test -x /usr/bin/obd
command -v timeout >/dev/null
command -v pgrep >/dev/null
if [ -f /root/.obd/cluster/obcluster/config.yaml ]; then
  timeout --signal=TERM --kill-after=5s 100s /usr/bin/obd cluster stop obcluster
fi
if pgrep -x observer >/dev/null; then
  echo "OceanBase observer is still running; refusing container removal" >&2
  exit 1
fi
`
