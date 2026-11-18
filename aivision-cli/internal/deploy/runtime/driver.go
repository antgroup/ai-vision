package runtime

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

// checkDeploymentDriver protects shared state from switching lifecycle owners.
// It intentionally does not infer ownership from names or adopt Compose data.
func (a *App) checkDeploymentDriver() error {
	for _, name := range []string{".compose-state.json", ".compose-cli.json"} {
		marker := filepath.Join(a.State.StateDir, name)
		if _, err := os.Lstat(marker); err == nil {
			return fmt.Errorf("deployment state is managed by Compose (%s); use the Compose deployment command", marker)
		} else if !os.IsNotExist(err) {
			return fmt.Errorf("check deployment owner: %w", err)
		}
	}
	return a.checkPhysicalDeploymentOwner()
}

// State paths are operator selectors, not proof of physical resource ownership.
// A different workdir must not let the Docker runtime stop a Compose container
// or purge a surviving Compose volume with the same configured physical name.
func (a *App) checkPhysicalDeploymentOwner() error {
	containers, volumes := map[string]bool{}, map[string]bool{}
	for _, component := range a.BP.OrderedComponents() {
		if component.Kind != "container" {
			continue
		}
		containers[a.ContainerName(component)] = true
		vars := a.ComponentVars(component)
		for _, raw := range component.Volumes {
			name := strings.SplitN(expand(raw, vars), ":", 2)[0]
			if namedDeploymentVolume(name) {
				volumes[name] = true
			}
		}
		for _, raw := range component.Mounts {
			kind, source := "", ""
			for _, part := range strings.Split(expand(raw, vars), ",") {
				key, value, _ := strings.Cut(strings.TrimSpace(part), "=")
				key = strings.ToLower(key)
				if key == "type" {
					kind = strings.ToLower(value)
				}
				if key == "source" || key == "src" {
					source = value
				}
			}
			if (kind == "" || kind == "volume") && namedDeploymentVolume(source) {
				volumes[source] = true
			}
		}
	}
	for _, resource := range []struct {
		kind  string
		names map[string]bool
	}{{"container", containers}, {"volume", volumes}} {
		names := make([]string, 0, len(resource.names))
		for name := range resource.names {
			names = append(names, name)
		}
		sort.Strings(names)
		for _, name := range names {
			labels, err := a.physicalResourceLabels(resource.kind, name)
			if err != nil {
				return err
			}
			for key := range labels {
				if strings.HasPrefix(key, "com.docker.compose.") {
					return fmt.Errorf("%s %s is managed by Compose; use --target compose with the original deployment (changing workdir does not transfer resource ownership)", resource.kind, name)
				}
			}
		}
	}
	return nil
}

func namedDeploymentVolume(name string) bool {
	return name != "" && !strings.ContainsAny(name, `/\\`) && !strings.HasPrefix(name, ".") && !strings.HasPrefix(name, "~")
}

// Keep inspect stdout/stderr separate: Docker can print [] on stdout when a
// requested object is absent. Permission/daemon/ambiguous failures must not be
// converted to absence, and inspect output may contain application credentials.
func (a *App) physicalResourceLabels(kind, name string) (map[string]string, error) {
	command := exec.CommandContext(a.Ctx, a.Docker.Bin, kind, "inspect", name)
	var stdout, stderr bytes.Buffer
	command.Stdout, command.Stderr = &stdout, &stderr
	if err := command.Run(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) && exit.ExitCode() == 1 && exactMissingResource(kind, name, stdout.String(), stderr.String()) {
			return nil, nil
		}
		return nil, fmt.Errorf("inspect %s ownership before Docker operations: %w", kind, err)
	}
	if kind == "container" {
		var result []struct {
			ID     string `json:"Id"`
			Config *struct {
				Labels map[string]string `json:"Labels"`
			} `json:"Config"`
		}
		if json.Unmarshal(stdout.Bytes(), &result) != nil || len(result) != 1 || result[0].ID == "" || result[0].Config == nil {
			return nil, fmt.Errorf("could not verify container ownership before Docker operations")
		}
		return result[0].Config.Labels, nil
	}
	var result []struct {
		Name   string            `json:"Name"`
		Labels map[string]string `json:"Labels"`
	}
	if json.Unmarshal(stdout.Bytes(), &result) != nil || len(result) != 1 || result[0].Name != name {
		return nil, fmt.Errorf("could not verify volume ownership before Docker operations")
	}
	return result[0].Labels, nil
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
