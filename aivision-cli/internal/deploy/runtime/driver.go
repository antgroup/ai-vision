package runtime

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
)

// checkDeploymentDriver protects shared state from switching lifecycle owners.
// It intentionally does not infer ownership from names or adopt Compose data.
func (a *App) checkDeploymentDriver() error {
	_, err := a.checkedDeploymentVolumes()
	return err
}

// checkedDeploymentVolumes checks the lifecycle owner of all configured Docker
// resources and returns the named volumes present before teardown begins.
func (a *App) checkedDeploymentVolumes() ([]string, error) {
	for _, name := range []string{".compose-state.json", ".compose-cli.json"} {
		marker := filepath.Join(a.State.StateDir, name)
		if _, err := os.Lstat(marker); err == nil {
			return nil, fmt.Errorf("deployment state is managed by Compose (%s); use the Compose deployment command", marker)
		} else if !os.IsNotExist(err) {
			return nil, fmt.Errorf("check deployment owner: %w", err)
		}
	}
	containers, volumes := map[string]bool{}, map[string]bool{}
	for _, component := range a.BP.OrderedComponents() {
		if component.Kind != "container" {
			continue
		}
		containers[a.ContainerName(component)] = true
		vars := a.ComponentVars(component)
		for _, raw := range component.Volumes {
			name, err := volumeName(expand(raw, vars))
			if err != nil {
				return nil, fmt.Errorf("component %s: %w", component.Name, err)
			}
			if name != "" {
				volumes[name] = true
			}
		}
		for _, raw := range component.Mounts {
			name, err := mountVolumeName(expand(raw, vars))
			if err != nil {
				return nil, fmt.Errorf("component %s: %w", component.Name, err)
			}
			if name != "" {
				volumes[name] = true
			}
		}
	}
	for _, name := range slices.Sorted(maps.Keys(containers)) {
		if _, err := a.checkPhysicalOwner("container", name); err != nil {
			return nil, err
		}
	}
	var present []string
	for _, name := range slices.Sorted(maps.Keys(volumes)) {
		exists, err := a.checkPhysicalOwner("volume", name)
		if err != nil {
			return nil, err
		}
		if exists {
			present = append(present, name)
		}
	}
	return present, nil
}

// Keep inspect stdout/stderr separate: Docker can print [] on stdout when a
// requested object is absent. Permission/daemon/ambiguous failures must not be
// converted to absence, and inspect output may contain application credentials.
func (a *App) checkPhysicalOwner(kind, name string) (bool, error) {
	command := exec.CommandContext(a.Ctx, a.Docker.Bin, kind, "inspect", name)
	var stdout, stderr bytes.Buffer
	command.Stdout, command.Stderr = &stdout, &stderr
	if err := command.Run(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) && exit.ExitCode() == 1 && exactMissingResource(kind, name, stdout.String(), stderr.String()) {
			return false, nil
		}
		return false, fmt.Errorf("inspect %s %s ownership before Docker operations: %w", kind, name, err)
	}
	var labels map[string]string
	if kind == "container" {
		var result []struct {
			ID     string `json:"Id"`
			Config *struct {
				Labels map[string]string `json:"Labels"`
			} `json:"Config"`
		}
		if json.Unmarshal(stdout.Bytes(), &result) != nil || len(result) != 1 || result[0].ID == "" || result[0].Config == nil {
			return false, fmt.Errorf("could not verify container ownership before Docker operations")
		}
		labels = result[0].Config.Labels
	} else {
		var result []struct {
			Name   string            `json:"Name"`
			Labels map[string]string `json:"Labels"`
		}
		if json.Unmarshal(stdout.Bytes(), &result) != nil || len(result) != 1 || result[0].Name != name {
			return false, fmt.Errorf("could not verify volume ownership before Docker operations")
		}
		labels = result[0].Labels
	}
	for key := range labels {
		if strings.HasPrefix(key, "com.docker.compose.") {
			return false, fmt.Errorf("%s %s is managed by Compose; use --target compose with the original deployment (changing workdir does not transfer resource ownership)", kind, name)
		}
	}
	return true, nil
}

func exactMissingResource(kind, name, stdout, stderr string) bool {
	output := strings.TrimSpace(stdout)
	if output != "" && output != "[]" {
		return false
	}
	prefix := `(?:Error response from daemon: |Error: )?`
	quoted := regexp.QuoteMeta(name)
	pattern := prefix + `No such (?:container|object): ` + quoted
	if kind == "volume" {
		pattern = prefix + `(?:[Nn]o such volume: ` + quoted + `|get ` + quoted + `: no such volume)`
	}
	return regexp.MustCompile(`^` + pattern + `$`).MatchString(strings.TrimSpace(stderr))
}
