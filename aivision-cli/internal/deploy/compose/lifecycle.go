package compose

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
)

type dockerResult struct {
	stdout, stderr string
	code           int
}
type dockerRun func(args []string, capture bool) (dockerResult, error)
type lifecycleController struct {
	run      dockerRun
	config   composeConfig
	identity deploymentIdentity
	command  []string
}

// RunLifecycle keeps configuration acceptance, storage ownership and bounded
// shutdown in Go. All subprocesses are the selected native Docker executable;
// Python is used only by bootstrap jobs inside their application images.
func RunLifecycle(ctx context.Context, docker, composeFile, envFile, action string, purge bool) error {
	run := func(args []string, capture bool) (dockerResult, error) {
		cmd := exec.CommandContext(ctx, docker, args...)
		cmd.Env = lifecycleEnvironment()
		var stdout, stderr bytes.Buffer
		if capture {
			cmd.Stdout, cmd.Stderr = &stdout, &stderr
		} else {
			cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
		}
		err := cmd.Run()
		result := dockerResult{stdout: stdout.String(), stderr: stderr.String()}
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			result.code = exit.ExitCode()
			return result, nil
		}
		if err != nil {
			return result, fmt.Errorf("could not execute Docker: %w", err)
		}
		return result, nil
	}
	return runLifecycle(run, composeFile, envFile, action, purge)
}

func lifecycleEnvironment() []string {
	var environment []string
	for _, value := range os.Environ() {
		if !strings.HasPrefix(value, "COMPOSE_") {
			environment = append(environment, value)
		}
	}
	return environment
}
func (c *lifecycleController) checked(args []string, capture bool) (dockerResult, error) {
	result, err := c.run(args, capture)
	if err != nil {
		return result, err
	}
	if result.code != 0 {
		return result, fmt.Errorf("Docker command failed (exit %d)", result.code)
	}
	return result, nil
}
func (c *lifecycleController) compose(args ...string) []string {
	return append(append([]string{}, c.command...), args...)
}

func runLifecycle(run dockerRun, composeFile, envFile, action string, purge bool) error {
	if action != "up" && action != "down" && action != "check" {
		return fmt.Errorf("unsupported guarded Compose action")
	}
	if purge && action != "down" {
		return fmt.Errorf("--purge is only valid with Compose down")
	}
	c := lifecycleController{run: run}
	if err := c.prerequisites(); err != nil {
		return err
	}
	file, err := canonicalPath(composeFile)
	if err != nil {
		return err
	}
	environment, err := canonicalPath(envFile)
	if err != nil {
		return err
	}
	// Compose loads reserved selectors from dotenv too. In particular, a
	// bootstrap profile there would start the offline initializer alongside
	// serving even after removing all ambient COMPOSE_* process variables.
	dotenv, err := os.ReadFile(environment)
	if err != nil {
		return fmt.Errorf("could not read selected Compose environment file: %w", err)
	}
	if hasReservedComposeDotenv(dotenv) {
		return fmt.Errorf("reserved COMPOSE_* keys are not allowed in the private environment file; select the product file and profile explicitly")
	}
	c.command = []string{"compose", "--file", file, "--env-file", environment}
	resolved, err := c.checked(c.compose("--profile", "bootstrap", "config", "--format", "json"), true)
	if err != nil {
		return err
	}
	c.config, c.identity, err = parseComposeConfig([]byte(resolved.stdout))
	if err != nil {
		return err
	}
	variables, err := readCLIConfiguration(file, c.identity, action == "up")
	if err != nil {
		return err
	}
	unlock, err := lockDeployment(c.identity.StateDir, action != "check")
	if err != nil {
		return err
	}
	defer unlock()
	if err := verifyIdentity(filepath.Join(c.identity.StateDir, ".compose-state.json"), c.identity); err != nil {
		return err
	}
	if err := c.checkOwnership(); err != nil {
		return err
	}
	if action == "check" {
		fmt.Println("AI Vision Compose prerequisites, configuration and ownership checks passed.")
		return nil
	}
	if action == "up" {
		if err := writeLifecycleJSON(filepath.Join(c.identity.StateDir, ".compose-state.json"), c.identity); err != nil {
			return err
		}
		if err := c.reserveMetadata(); err != nil {
			return err
		}
		if err := saveCLIConfiguration(c.identity.StateDir, file, variables, "pending"); err != nil {
			return err
		}
		for _, service := range c.config.Services {
			for _, mount := range service.Volumes {
				if mount.Type == "bind" && !mount.ReadOnly {
					if err := os.MkdirAll(mount.Source, 0700); err != nil {
						return err
					}
				}
			}
		}
		if err := c.stopServices(); err != nil {
			return err
		}
		if _, err := c.checked(c.compose("rm", "--force", "database-init", "data-backend-ready", "backend-bootstrap"), false); err != nil {
			return err
		}
		if c.identity.Managed && c.identity.Backend == "seekdb" {
			if _, err := c.checked(c.compose("--profile", "bootstrap", "run", "--rm", "--no-deps", "database-initialize"), false); err != nil {
				return err
			}
		}
		if _, err := c.checked(c.compose("up", "--detach", "--wait", "--wait-timeout", "1200"), false); err != nil {
			return err
		}
		if err := saveCLIConfiguration(c.identity.StateDir, file, variables, "ready"); err != nil {
			return err
		}
		fmt.Println("AI Vision services and initialization tasks are ready. Verify authenticated telemetry readback.")
		return nil
	}
	if err := c.stopServices(); err != nil {
		return err
	}
	args := []string{"--profile", "bootstrap", "down"}
	if purge {
		args = append(args, "--volumes")
	}
	if _, err := c.checked(c.compose(args...), false); err != nil {
		return err
	}
	if purge {
		fmt.Println("Named volumes removed. Bind-mounted metadata and deployment identity were retained.")
	}
	return nil
}

// Compose dotenv accepts both assignment separators and Unicode whitespace
// around keys. Inspect complete keys rather than an ASCII-only line regexp.
// Quoted values can span lines: selector-looking literal text inside them is
// not another assignment and must remain usable as application configuration.
func hasReservedComposeDotenv(data []byte) bool {
	var quote byte
	for _, line := range strings.Split(strings.TrimPrefix(string(data), "\ufeff"), "\n") {
		remainder := line
		for {
			if quote != 0 {
				end := -1
				for index := 0; index < len(remainder); index++ {
					if remainder[index] == '\\' {
						index++
						continue
					}
					if remainder[index] == quote {
						end = index
						break
					}
				}
				if end < 0 {
					break
				}
				remainder, quote = remainder[end+1:], 0
			}
			statement := strings.TrimSpace(remainder)
			if statement == "" || strings.HasPrefix(statement, "#") {
				break
			}
			separator := strings.IndexAny(statement, "=:")
			if separator < 0 {
				break
			}
			key := strings.TrimSpace(statement[:separator])
			// Native Compose also accepts CR/form feed after export. Treat
			// every whitespace separator conservatively, not just space/tab.
			if words := strings.Fields(key); len(words) > 1 && words[0] == "export" {
				key = strings.TrimSpace(key[len("export"):])
			}
			if strings.HasPrefix(key, "COMPOSE_") {
				return true
			}
			value := strings.TrimSpace(statement[separator+1:])
			if value == "" || (value[0] != '\'' && value[0] != '"') {
				break
			}
			quote, remainder = value[0], value[1:]
		}
	}
	return false
}

func (c *lifecycleController) prerequisites() error {
	result, err := c.checked([]string{"compose", "version", "--short"}, true)
	if err != nil {
		return fmt.Errorf("Docker Compose 2.27 or newer is required: %w", err)
	}
	version := regexp.MustCompile(`^v?([0-9]+)\.([0-9]+)\.([0-9]+)(?:[-+][0-9A-Za-z.-]+)?$`).FindStringSubmatch(strings.TrimSpace(result.stdout))
	if len(version) != 4 {
		return fmt.Errorf("Docker Compose 2.27 or newer is required")
	}
	major, _ := strconv.Atoi(version[1])
	minor, _ := strconv.Atoi(version[2])
	if major < 2 || (major == 2 && minor < 27) {
		return fmt.Errorf("Docker Compose 2.27 or newer is required")
	}
	result, err = c.checked([]string{"version", "--format", "{{.Server.Version}}"}, true)
	if err != nil || strings.TrimSpace(result.stdout) == "" {
		return fmt.Errorf("Docker daemon is not reachable")
	}
	return nil
}

func (c *lifecycleController) stopServices() error {
	services := map[string]bool{}
	for name, service := range c.config.Services {
		if service.Restart == "always" || service.Restart == "unless-stopped" || service.Restart == "on-failure" {
			services[name] = true
		}
	}
	pendingPath := filepath.Join(c.identity.StateDir, ".compose-stop-pending.json")
	pending := map[string]string{}
	if data, err := os.ReadFile(pendingPath); err == nil {
		if json.Unmarshal(data, &pending) != nil || pending == nil {
			return fmt.Errorf("invalid pending Compose stop record; inspect it before retrying")
		}
		for id, role := range pending {
			if id == "" || !services[role] {
				return fmt.Errorf("invalid pending Compose stop record; inspect it before retrying")
			}
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	listing, err := c.checked(c.compose(append([]string{"ps", "--all", "--quiet"}, sortedKeys(services)...)...), true)
	if err != nil {
		return err
	}
	ids := strings.Fields(listing.stdout)
	var containers []containerInfo
	if len(ids) > 0 {
		result, err := c.checked(append([]string{"container", "inspect"}, ids...), true)
		if err != nil {
			return err
		}
		containers, err = decodeContainers(result.stdout)
		if err != nil {
			return err
		}
		if !sameContainerIDs(containers, ids) {
			return fmt.Errorf("could not inspect exact serving container identities before stop")
		}
	}
	selected := map[string]string{}
	for _, container := range containers {
		role := container.Config.Labels["com.docker.compose.service"]
		if !services[role] || container.Config.Labels["com.docker.compose.project"] != c.config.Name {
			return fmt.Errorf("could not verify serving container ownership before stop")
		}
		if container.State.Running == nil {
			return fmt.Errorf("could not verify serving container running state")
		}
		if *container.State.Running || container.State.Restarting || pending[container.ID] != "" {
			selected[container.ID] = role
		}
	}
	if len(selected) > 0 {
		if err := writeLifecycleJSON(pendingPath, selected); err != nil {
			return err
		}
	}
	if _, err := c.checked(c.compose("stop"), false); err != nil {
		return err
	}
	if len(selected) > 0 {
		result, err := c.checked(append([]string{"container", "inspect"}, sortedKeys(selected)...), true)
		if err != nil {
			return err
		}
		stopped, err := decodeContainers(result.stdout)
		if err != nil {
			return err
		}
		if !sameContainerIDs(stopped, sortedKeys(selected)) {
			return fmt.Errorf("could not verify stopped services; containers and storage were retained")
		}
		for _, container := range stopped {
			s := container.State
			if s.Running == nil || *s.Running || s.Restarting || s.Dead || s.OOMKilled == nil || *s.OOMKilled || s.ExitCode == nil || *s.ExitCode != 0 || s.Status != "exited" {
				return fmt.Errorf("service %s failed to stop cleanly; containers and storage were retained. Inspect logs; restart and stop successfully or explicitly remove the failed container before retrying", selected[container.ID])
			}
		}
	}
	if err := os.Remove(pendingPath); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}
func sameContainerIDs(containers []containerInfo, identifiers []string) bool {
	if len(containers) != len(identifiers) {
		return false
	}
	wanted := map[string]bool{}
	for _, id := range identifiers {
		if id == "" || wanted[id] {
			return false
		}
		wanted[id] = true
	}
	for _, container := range containers {
		if !wanted[container.ID] {
			return false
		}
		delete(wanted, container.ID)
	}
	return len(wanted) == 0
}

func readCLIConfiguration(composeFile string, current deploymentIdentity, install bool) (map[string]string, error) {
	path := filepath.Join(filepath.Dir(composeFile), ".cli-vars.json")
	if _, err := os.Lstat(path); os.IsNotExist(err) {
		return nil, nil
	} else if err != nil {
		return nil, err
	}
	if err := privateFile(path); err != nil {
		return nil, fmt.Errorf("invalid private CLI configuration sidecar")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var value configuration
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&value) != nil || value.Schema != 1 || value.Namespace != current.Namespace || !filepath.IsAbs(value.StateDir) || value.Vars == nil {
		return nil, fmt.Errorf("invalid CLI configuration sidecar; regenerate the private deployment bundle")
	}
	if decoder.Decode(new(any)) != io.EOF {
		return nil, fmt.Errorf("invalid CLI configuration sidecar; regenerate the private deployment bundle")
	}
	state, err := canonicalPath(value.StateDir)
	if err != nil || state != current.StateDir {
		return nil, fmt.Errorf("CLI sidecar belongs to a different deployment state")
	}
	if install {
		bundle := filepath.Dir(composeFile)
		if filepath.Dir(bundle) != filepath.Join(current.StateDir, "compose", "bundles") {
			return nil, fmt.Errorf("CLI deployment bundle must be inside this deployment's private bundle store")
		}
		if err := verifyBundle(bundle); err != nil {
			return nil, err
		}
	}
	return value.Vars, nil
}
func saveCLIConfiguration(state, file string, variables map[string]string, status string) error {
	if variables == nil {
		return nil
	}
	if err := writeLifecycleJSON(filepath.Join(state, "vars.json"), variables); err != nil {
		return err
	}
	return writeLifecycleJSON(filepath.Join(state, pointerName), savedProject{Schema: 1, Bundle: filepath.Dir(file), Status: status})
}
func writeLifecycleJSON(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".compose-state-")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if _, err = file.Write(append(data, '\n')); err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(file.Name(), path)
}
func lockDeployment(state string, mutate bool) (func(), error) {
	path := filepath.Join(state, ".compose.lock")
	info, err := os.Lstat(path)
	if err == nil && !info.Mode().IsRegular() {
		return nil, fmt.Errorf("Compose lock must be a regular file")
	}
	if err != nil && !os.IsNotExist(err) {
		return nil, err
	}
	if !mutate && os.IsNotExist(err) {
		return func() {}, nil
	}
	flags := os.O_RDONLY
	if mutate {
		if err := privateDir(state); err != nil {
			return nil, err
		}
		flags = os.O_RDWR | os.O_CREATE
	}
	file, err := os.OpenFile(path, flags, 0600)
	if err != nil {
		return nil, err
	}
	operation := syscall.LOCK_SH | syscall.LOCK_NB
	if mutate {
		operation = syscall.LOCK_EX | syscall.LOCK_NB
	}
	if err := syscall.Flock(int(file.Fd()), operation); err != nil {
		file.Close()
		return nil, fmt.Errorf("another operation is using this deployment: %w", err)
	}
	return func() { _ = syscall.Flock(int(file.Fd()), syscall.LOCK_UN); _ = file.Close() }, nil
}
