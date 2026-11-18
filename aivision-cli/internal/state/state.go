// Package state persists everything the CLI records about a deployment
// (pidfiles, once-markers, deploy metadata) so status, logs and destroy can
// operate even when the blueprint is not at hand.
package state

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
)

// State tracks everything aiv persists about a deployment so that status,
// logs and destroy can operate even when the blueprint is not at hand.
type State struct {
	Namespace   string
	Root        string // <workdir>/<namespace>
	StateDir    string // Root/state
	LogDir      string // Root/logs
	ArtifactDir string // Root/artifacts
}

// DeployMeta is written to state/meta.json on every successful cast.
type DeployMeta struct {
	DatastoreBackend string            `json:"datastoreBackend,omitempty"`
	Namespace        string            `json:"namespace"`
	Blueprint        string            `json:"blueprint"`
	SourceRoot       string            `json:"sourceRoot"`
	DeployedAt       string            `json:"deployedAt"`
	Components       map[string]string `json:"components"` // name -> kind
	Containers       map[string]string `json:"containers"` // component -> container name
	Discovered       map[string]string `json:"discovered"` // var -> value (e.g. ORG_ID)
}

func NewState(workdir, namespace string) *State {
	root := filepath.Join(blueprint.CleanPath(workdir, nil), namespace)
	return &State{
		Namespace:   namespace,
		Root:        root,
		StateDir:    filepath.Join(root, "state"),
		LogDir:      filepath.Join(root, "logs"),
		ArtifactDir: filepath.Join(root, "artifacts"),
	}
}

func (s *State) EnsureDirs() error {
	for _, d := range []string{s.StateDir, s.LogDir, s.ArtifactDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			return fmt.Errorf("create dir %s: %w", d, err)
		}
	}
	return nil
}

// PidFile returns the pidfile path for a process component.
func (s *State) PidFile(comp string) string {
	return filepath.Join(s.StateDir, comp+".pid")
}

// LogFile returns the log path for a component.
func (s *State) LogFile(comp string) string {
	return filepath.Join(s.LogDir, comp+".log")
}

func (s *State) WritePid(comp string, pid int) error {
	return os.WriteFile(s.PidFile(comp), []byte(strconv.Itoa(pid)), 0o644)
}

func (s *State) ReadPid(comp string) int {
	b, err := os.ReadFile(s.PidFile(comp))
	if err != nil {
		return 0
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil {
		return 0
	}
	return pid
}

func (s *State) RemovePid(comp string) {
	_ = os.Remove(s.PidFile(comp))
}

// PidAlive reports whether a pid exists and is running. It probes with
// signal 0 (syscall.Kill) which never actually delivers a signal.
func PidAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	return syscall.Kill(pid, 0) == nil
}

// markerFile returns the once-marker path for a component step.
func (s *State) markerFile(comp, step string) string {
	safe := strings.Map(func(r rune) rune {
		if r == '/' || r == ' ' {
			return '_'
		}
		return r
	}, step)
	if len(safe) > 48 {
		safe = safe[:48]
	}
	return filepath.Join(s.StateDir, fmt.Sprintf("%s.%s.done", comp, safe))
}

func (s *State) MarkerDone(comp, step string) bool {
	_, err := os.Stat(s.markerFile(comp, step))
	return err == nil
}

func (s *State) MarkDone(comp, step string) error {
	return os.WriteFile(s.markerFile(comp, step), []byte(time.Now().Format(time.RFC3339)), 0o644)
}

// ClearMarkers removes all once-markers (used by destroy --purge and --force-bootstrap).
func (s *State) ClearMarkers() {
	entries, _ := os.ReadDir(s.StateDir)
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".done") {
			_ = os.Remove(filepath.Join(s.StateDir, e.Name()))
		}
	}
}

func (s *State) metaFile() string { return filepath.Join(s.StateDir, "meta.json") }

func (s *State) SaveMeta(m DeployMeta) error {
	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(s.metaFile(), b, 0o644)
}

func (s *State) LoadMeta() (*DeployMeta, error) {
	b, err := os.ReadFile(s.metaFile())
	if err != nil {
		return nil, err
	}
	var m DeployMeta
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, err
	}
	return &m, nil
}

// varsFile holds the user-supplied variable overrides that produced the last
// successful cast, so status/destroy/redeploy reuse them without the caller
// re-passing every --var. It may contain secrets, hence 0600.
func (s *State) varsFile() string { return filepath.Join(s.StateDir, "vars.json") }

// SaveVars persists the override set (not the blueprint defaults).
func (s *State) SaveVars(vars map[string]string) error {
	if err := os.MkdirAll(s.StateDir, 0o755); err != nil {
		return err
	}
	b, err := json.MarshalIndent(vars, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(s.varsFile(), b, 0o600)
}

// LoadVars returns the persisted overrides, or an empty map when none exist.
func (s *State) LoadVars() (map[string]string, error) {
	b, err := os.ReadFile(s.varsFile())
	if err != nil {
		if os.IsNotExist(err) {
			return map[string]string{}, nil
		}
		return nil, err
	}
	out := map[string]string{}
	if err := json.Unmarshal(b, &out); err != nil {
		return nil, fmt.Errorf("parse %s: %w", s.varsFile(), err)
	}
	return out, nil
}

// RemoveAll deletes the entire namespace state root (destroy --purge).
func (s *State) RemoveAll() error {
	return os.RemoveAll(s.Root)
}
