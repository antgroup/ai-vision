package compose

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"time"
)

const metadataOwnerName = ".aivision-compose-owner.json"

type composeMount struct {
	Type     string `json:"type"`
	Source   string `json:"source"`
	Target   string `json:"target"`
	ReadOnly bool   `json:"read_only"`
}
type composeService struct {
	ContainerName string            `json:"container_name"`
	Restart       string            `json:"restart"`
	Volumes       []composeMount    `json:"volumes"`
	Environment   map[string]string `json:"environment"`
}
type composeVolume struct {
	Name   string            `json:"name"`
	Labels map[string]string `json:"labels"`
}
type composeDeployment struct {
	Backend      string `json:"backend"`
	Managed      *bool  `json:"managed"`
	Namespace    string `json:"namespace"`
	StateDir     string `json:"state_dir"`
	HistoryStart string `json:"history_start"`
}
type composeConfig struct {
	Name       string                    `json:"name"`
	Services   map[string]composeService `json:"services"`
	Volumes    map[string]composeVolume  `json:"volumes"`
	Deployment composeDeployment         `json:"x-aivision"`
}

// Keep this schema identical to the original standalone Compose identity. It
// intentionally excludes credentials and images, which bootstrap re-verifies.
type deploymentIdentity struct {
	Namespace      string            `json:"namespace"`
	StateDir       string            `json:"state_dir"`
	Backend        string            `json:"backend"`
	Managed        bool              `json:"managed"`
	HistoryStart   string            `json:"history_start"`
	Volumes        map[string]string `json:"volumes"`
	MetadataMounts map[string]string `json:"metadata_mounts"`
	Database       map[string]string `json:"database"`
}

type containerInfo struct {
	ID     string `json:"Id"`
	Config struct {
		Labels map[string]string `json:"Labels"`
	} `json:"Config"`
	Mounts []struct {
		Type        string `json:"Type"`
		Name        string `json:"Name"`
		Source      string `json:"Source"`
		Destination string `json:"Destination"`
	} `json:"Mounts"`
	State struct {
		Running    *bool  `json:"Running"`
		Restarting bool   `json:"Restarting"`
		Dead       bool   `json:"Dead"`
		OOMKilled  *bool  `json:"OOMKilled"`
		ExitCode   *int   `json:"ExitCode"`
		Status     string `json:"Status"`
	} `json:"State"`
}

func parseComposeConfig(data []byte) (composeConfig, deploymentIdentity, error) {
	var config composeConfig
	current := deploymentIdentity{Volumes: map[string]string{}, MetadataMounts: map[string]string{}, Database: map[string]string{}}
	if err := json.Unmarshal(data, &config); err != nil {
		return config, current, fmt.Errorf("could not read resolved Compose configuration")
	}
	d := config.Deployment
	if (d.Backend != "oceanbase" && d.Backend != "seekdb") || d.Managed == nil {
		return config, current, fmt.Errorf("use a reviewed AI Vision product Compose file")
	}
	if !regexp.MustCompile(`^[a-z0-9][a-z0-9_-]*$`).MatchString(d.Namespace) || d.Namespace != config.Name {
		return config, current, fmt.Errorf("NAMESPACE must match the Compose project name")
	}
	bound, err := time.Parse(time.RFC3339Nano, d.HistoryStart)
	_, offset := bound.Zone()
	if err != nil || offset != 0 {
		return config, current, fmt.Errorf("set a fixed UTC AIVISION_NATIVE_TRACE_HISTORY_START covering all retained traces")
	}
	if !filepath.IsAbs(d.StateDir) || filepath.Clean(d.StateDir) == string(filepath.Separator) {
		return config, current, fmt.Errorf("STATE_DIR must be an absolute persistent deployment directory")
	}
	state, err := canonicalPath(d.StateDir)
	if err != nil {
		return config, current, err
	}
	current.Namespace, current.StateDir, current.Backend, current.Managed, current.HistoryStart = d.Namespace, state, d.Backend, *d.Managed, d.HistoryStart
	owner := storageOwner(current)
	physical := map[string]bool{}
	for key, volume := range config.Volumes {
		if volume.Name == "" || physical[volume.Name] || !matchesOwner(volume.Labels, owner) {
			return config, current, fmt.Errorf("use reviewed Compose volume ownership labels and unique physical names; regenerate the Compose file")
		}
		physical[volume.Name] = true
		current.Volumes[key] = volume.Name
	}
	for role, service := range config.Services {
		for _, mount := range service.Volumes {
			if mount.Type == "volume" {
				if _, exists := config.Volumes[mount.Source]; !exists {
					return config, current, fmt.Errorf("service references an undeclared persistent volume")
				}
			}
			if mount.Target == "/var/lib/signoz" {
				if mount.Type != "bind" || !filepath.IsAbs(mount.Source) || filepath.Clean(mount.Source) == string(filepath.Separator) {
					return config, current, fmt.Errorf("metadata must use an absolute persistent bind directory")
				}
				// Preserve the resolved spelling in the compatibility identity; all
				// ownership comparisons separately canonicalize the filesystem path.
				current.MetadataMounts[role] = mount.Source
			}
		}
	}
	database, exists := config.Services["database-init"]
	if !exists || !regexp.MustCompile(`^[A-Za-z0-9_-]+$`).MatchString(database.Environment["OB_TENANT_PASSWORD"]) {
		return config, current, fmt.Errorf("OB_TENANT_PASSWORD must use letters, digits, underscore or hyphen")
	}
	for _, key := range []string{"OB_HOST", "OB_HOST_PORT", "OB_USER"} {
		if database.Environment[key] == "" {
			return config, current, fmt.Errorf("database initialization configuration is incomplete")
		}
		current.Database[key] = database.Environment[key]
	}
	for _, key := range []string{"AGENTLENS_OB_URL", "SIGNOZ_OB_URL"} {
		if value, exists := config.Services["diagnosis"].Environment[key]; exists {
			parsed, err := url.Parse(value)
			if err != nil || parsed.User == nil || parsed.User.Username() != database.Environment["OB_USER"] {
				return config, current, fmt.Errorf("set OB_USER_URL to the percent-encoded OB_USER so all services use the same account")
			}
		}
	}
	return config, current, nil
}

func verifyIdentity(path string, current deploymentIdentity) error {
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("deployment identity must be a regular file")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var prior deploymentIdentity
	if json.Unmarshal(data, &prior) != nil || !reflect.DeepEqual(prior, current) {
		return fmt.Errorf("deployment identity, history bound or storage changed; reuse the original configuration (a new backend requires a separate namespace and data migration)")
	}
	return nil
}

func storageOwner(identity deploymentIdentity) map[string]string {
	return map[string]string{"io.aivision.product": "aivision", "io.aivision.namespace": identity.Namespace, "io.aivision.state-dir": identity.StateDir}
}
func matchesOwner(labels, owner map[string]string) bool {
	if labels["io.aivision.product"] != owner["io.aivision.product"] || labels["io.aivision.namespace"] != owner["io.aivision.namespace"] || !filepath.IsAbs(labels["io.aivision.state-dir"]) {
		return false
	}
	path, err := canonicalPath(labels["io.aivision.state-dir"])
	return err == nil && path == owner["io.aivision.state-dir"]
}
func (c *lifecycleController) metadataDirectories() ([]string, error) {
	unique := map[string]bool{}
	for _, source := range c.identity.MetadataMounts {
		path, err := canonicalPath(source)
		if err != nil {
			return nil, err
		}
		unique[path] = true
	}
	return sortedKeys(unique), nil
}
func (c *lifecycleController) checkMetadataOwner(directory string) error {
	marker := filepath.Join(directory, metadataOwnerName)
	info, err := os.Lstat(marker)
	if err == nil {
		if !info.Mode().IsRegular() {
			return fmt.Errorf("metadata ownership marker must be a regular file")
		}
		data, err := os.ReadFile(marker)
		if err != nil {
			return err
		}
		var owner map[string]string
		if json.Unmarshal(data, &owner) != nil || !reflect.DeepEqual(owner, storageOwner(c.identity)) {
			return fmt.Errorf("metadata storage belongs to another deployment or state directory")
		}
		return nil
	}
	if !os.IsNotExist(err) {
		return err
	}
	entries, err := os.ReadDir(directory)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if len(entries) > 0 {
		return fmt.Errorf("metadata storage is nonempty with unknown ownership; automatic adoption is disabled")
	}
	return nil
}
func (c *lifecycleController) reserveMetadata() error {
	directories, err := c.metadataDirectories()
	if err != nil {
		return err
	}
	for _, directory := range directories {
		if err := os.MkdirAll(directory, 0700); err != nil {
			return err
		}
		if err := c.checkMetadataOwner(directory); err != nil {
			return err
		}
		file, err := os.OpenFile(filepath.Join(directory, metadataOwnerName), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if os.IsExist(err) {
			if err := c.checkMetadataOwner(directory); err != nil {
				return err
			}
			continue
		}
		if err != nil {
			return err
		}
		data, _ := json.Marshal(storageOwner(c.identity))
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
	}
	return nil
}

func (c *lifecycleController) checkOwnership() error {
	for _, name := range []string{"datastore-backend", "meta.json"} {
		if _, err := os.Lstat(filepath.Join(c.identity.StateDir, name)); err == nil {
			return fmt.Errorf("deployment state belongs to the original Docker CLI; automatic adoption is disabled")
		} else if !os.IsNotExist(err) {
			return err
		}
	}
	if err := c.checkStorageOwnership(); err != nil {
		return err
	}
	for _, role := range sortedKeys(c.config.Services) {
		service := c.config.Services[role]
		if service.ContainerName == "" {
			continue
		}
		result, err := c.run([]string{"container", "inspect", service.ContainerName}, true)
		if err != nil {
			return fmt.Errorf("could not inspect deployment container")
		}
		if result.code != 0 {
			// Only an explicit not-found response is absence. Permission and
			// daemon errors must not permit a second resource to be created.
			if !missingInspect(result, []string{service.ContainerName}, false) {
				return fmt.Errorf("could not inspect deployment container")
			}
			continue
		}
		containers, err := decodeContainers(result.stdout)
		if err != nil || len(containers) != 1 {
			return fmt.Errorf("could not read existing deployment container")
		}
		container := containers[0]
		if container.Config.Labels["com.docker.compose.project"] != c.config.Name || container.Config.Labels["com.docker.compose.service"] != role {
			return fmt.Errorf("container %s belongs to another deployment; automatic adoption is disabled", service.ContainerName)
		}
		for _, mount := range service.Volumes {
			found := false
			for _, actual := range container.Mounts {
				if actual.Destination != mount.Target {
					continue
				}
				if mount.Type == "volume" {
					found = actual.Name == c.config.Volumes[mount.Source].Name
				} else if mount.Type == "bind" && mount.Target == "/var/lib/signoz" {
					a, ae := canonicalPath(actual.Source)
					b, be := canonicalPath(mount.Source)
					found = actual.Source != "" && ae == nil && be == nil && a == b
				} else {
					found = true
				}
			}
			if !found && (mount.Type == "volume" || mount.Target == "/var/lib/signoz") {
				return fmt.Errorf("existing container uses different persistent storage; automatic adoption is disabled")
			}
		}
	}
	return nil
}
func (c *lifecycleController) checkStorageOwnership() error {
	directories, err := c.metadataDirectories()
	if err != nil {
		return err
	}
	binds := map[string]bool{}
	for _, directory := range directories {
		if err := c.checkMetadataOwner(directory); err != nil {
			return err
		}
		binds[directory] = true
	}
	result, err := c.checked([]string{"volume", "ls", "--format", "{{.Name}}"}, true)
	if err != nil {
		return err
	}
	keys := map[string]string{}
	for key, volume := range c.config.Volumes {
		keys[volume.Name] = key
	}
	var existing []string
	for _, name := range strings.Fields(result.stdout) {
		if _, ok := keys[name]; ok {
			existing = append(existing, name)
		}
	}
	if len(existing) > 0 {
		sort.Strings(existing)
		result, err := c.checked(append([]string{"volume", "inspect"}, existing...), true)
		if err != nil {
			return err
		}
		var volumes []struct {
			Name   string
			Labels map[string]string
		}
		if json.Unmarshal([]byte(result.stdout), &volumes) != nil || len(volumes) != len(existing) {
			return fmt.Errorf("could not inspect persistent volumes")
		}
		seen := map[string]bool{}
		for _, volume := range volumes {
			key, exists := keys[volume.Name]
			if !exists || seen[volume.Name] || !matchesOwner(volume.Labels, storageOwner(c.identity)) || volume.Labels["com.docker.compose.project"] != c.config.Name || volume.Labels["com.docker.compose.volume"] != key {
				return fmt.Errorf("named volume belongs to another deployment or has unknown ownership; automatic adoption is disabled")
			}
			seen[volume.Name] = true
		}
	}
	containers, err := c.inspectStorageContainers()
	if err != nil {
		return err
	}
	for _, container := range containers {
		if container.Config.Labels["com.docker.compose.project"] == c.config.Name {
			continue
		}
		for _, mount := range container.Mounts {
			_, volume := keys[mount.Name]
			bind := false
			if mount.Type == "bind" && mount.Source != "" {
				path, err := canonicalPath(mount.Source)
				if err != nil {
					return err
				}
				bind = binds[path]
			}
			if volume || bind {
				return fmt.Errorf("persistent storage belongs to another deployment; automatic adoption is disabled")
			}
		}
	}
	return nil
}
func (c *lifecycleController) inspectStorageContainers() ([]containerInfo, error) {
	for attempt := 0; attempt < 3; attempt++ {
		listing, err := c.checked([]string{"ps", "--all", "--quiet", "--no-trunc"}, true)
		if err != nil {
			return nil, err
		}
		identifiers := strings.Fields(listing.stdout)
		if len(identifiers) == 0 {
			return nil, nil
		}
		result, err := c.run(append([]string{"container", "inspect"}, identifiers...), true)
		if err != nil {
			return nil, fmt.Errorf("could not inspect containers to verify storage ownership")
		}
		if result.code == 0 {
			containers, err := decodeContainers(result.stdout)
			if err != nil || !sameContainerIDs(containers, identifiers) {
				return nil, fmt.Errorf("could not inspect the complete container snapshot to verify storage ownership")
			}
			return containers, nil
		}
		if !missingInspect(result, identifiers, true) {
			return nil, fmt.Errorf("could not inspect containers to verify storage ownership")
		}
	}
	return nil, fmt.Errorf("containers kept disappearing during storage ownership checks; retry the operation")
}
func decodeContainers(output string) ([]containerInfo, error) {
	var containers []containerInfo
	if json.Unmarshal([]byte(output), &containers) != nil {
		return nil, fmt.Errorf("could not decode container inspection")
	}
	return containers, nil
}
func missingInspect(result dockerResult, identifiers []string, hexOnly bool) bool {
	if result.code != 1 || strings.TrimSpace(result.stderr) == "" {
		return false
	}
	allowed := map[string]bool{}
	for _, id := range identifiers {
		allowed[id] = true
	}
	pattern := `^(?:Error response from daemon: |Error: )?No such (?:container|object): ([A-Za-z0-9_.-]+)$`
	if hexOnly {
		pattern = `^(?:Error response from daemon: |Error: )?No such (?:container|object): ([0-9a-f]+)$`
	}
	matcher := regexp.MustCompile(pattern)
	for _, line := range strings.Split(strings.TrimSpace(result.stderr), "\n") {
		matches := matcher.FindStringSubmatch(strings.TrimSpace(line))
		if len(matches) != 2 || !allowed[matches[1]] {
			return false
		}
	}
	return true
}
func sortedKeys[V any](values map[string]V) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}
