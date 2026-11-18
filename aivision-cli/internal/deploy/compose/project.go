// Package compose adapts the CLI to the canonical product Compose lifecycle.
// Go owns lifecycle guards; native Docker Compose performs container operations.
package compose

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/generate"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
)

const pointerName = ".compose-cli.json"

type Owner string

const (
	Unowned      Owner = ""
	ComposeOwner Owner = "compose"
	LegacyOwner  Owner = "legacy"
)

// Ownership checks marker presence without guessing from resource names. The
// Go lifecycle independently checks physical resources before mutations.
func Ownership(stateDir string) (Owner, error) {
	legacy, composed := false, false
	for _, name := range []string{"meta.json", "datastore-backend", ".compose-state.json", pointerName} {
		_, err := os.Lstat(filepath.Join(stateDir, name))
		if err != nil && !os.IsNotExist(err) {
			return Unowned, fmt.Errorf("inspect deployment ownership: %w", err)
		}
		if err == nil {
			if name == "meta.json" || name == "datastore-backend" {
				legacy = true
			} else {
				composed = true
			}
		}
	}
	if legacy && composed {
		return Unowned, fmt.Errorf("conflicting legacy and Compose deployment ownership; inspect the original deployment before continuing")
	}
	if legacy {
		return LegacyOwner, nil
	}
	if composed {
		return ComposeOwner, nil
	}
	return Unowned, nil
}

// Supported identifies the reviewed image blueprint. Source/process and
// non-product custom blueprints retain their existing development runtime.
func Supported(app *runtime.App) bool {
	if app.BP.Metadata.Product != "aivision" {
		return false
	}
	for _, component := range app.BP.OrderedComponents() {
		if component.Kind != "container" || component.Image == "" {
			return false
		}
	}
	return true
}

// Select enforces the explicitly chosen target; ownership never changes it.
func Select(app *runtime.App, explicit bool, _ string) (bool, error) {
	owner, err := Ownership(app.State.StateDir)
	if err != nil {
		return false, err
	}
	if !explicit {
		if owner == ComposeOwner {
			return false, fmt.Errorf("namespace is managed by Compose; select --target compose and its original product blueprint (Docker never adopts Compose installations)")
		}
		return false, nil
	}
	if owner == LegacyOwner {
		return false, fmt.Errorf("legacy Docker installation detected; --target compose never adopts Docker installations. Use the default Docker target, or a separate namespace with separate storage")
	}
	if !Supported(app) {
		return false, fmt.Errorf("--target compose requires the product image blueprint; source/process and custom blueprints use their Docker development runtime")
	}
	return true, nil
}

type Project struct{ app *runtime.App }

func New(app *runtime.App) *Project { return &Project{app: app} }

type Options struct {
	Component string
	Tail      int
	Follow    bool
	Purge     bool
}

type savedProject struct {
	Schema int    `json:"schema"`
	Bundle string `json:"bundle"`
	Status string `json:"status"`
}

type configuration struct {
	Schema    int               `json:"schema"`
	Namespace string            `json:"namespace"`
	StateDir  string            `json:"state_dir"`
	Vars      map[string]string `json:"vars"`
}

// Files preserves derived templates while pinning actual selected images and
// datastore defaults. Credentials stay literal through runtime.NewApp.
func (p *Project) files() ([]generate.File, error) {
	files, _, err := generate.Render(p.app, generate.Compose, generate.Options{})
	if err != nil {
		return nil, err
	}
	// buildApp adds role references for template expansion after normalizing
	// input layers. Those references are not user overrides: normalizing them
	// again would replace the actual value with a self-reference.
	raw := map[string]string{}
	for key, value := range p.app.BP.Config.Vars {
		legacy := components.VarKey(key)
		if legacy != key && value == "${"+legacy+"}" {
			continue
		}
		raw[key] = value
	}
	values := components.NormalizeVars(raw)
	for _, key := range []string{"NAMESPACE", "STATE_DIR", "LOG_DIR", "ARTIFACT_DIR", "SOURCE_ROOT", "WORKDIR", "BLUEPRINT_PATH"} {
		delete(values, key)
	}
	for _, key := range []string{"FE_IMAGE", "BE_IMAGE", "AGENTLENS_IMAGE", "GATEWAY_IMAGE", "SIGNOZ_IMAGE", "OB_IMAGE", "OB_USER"} {
		values[key] = p.app.Vars[key]
	}
	state, err := canonicalPath(p.app.State.StateDir)
	if err != nil {
		return nil, err
	}
	data, err := json.MarshalIndent(configuration{Schema: 1, Namespace: p.app.BP.Metadata.Namespace, StateDir: state, Vars: values}, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(files, generate.File{Path: ".cli-vars.json", Content: append(data, '\n')}), nil
}

// Publish atomically installs an immutable bundle. It never changes the saved
// pointer or vars: only the guarded lifecycle accepts configuration under its lock.
func (p *Project) Publish() (string, error) {
	files, err := p.files()
	if err != nil {
		return "", err
	}
	root, err := canonicalPath(p.app.State.StateDir)
	if err != nil {
		return "", err
	}
	for _, dir := range []string{root, filepath.Join(root, "compose"), filepath.Join(root, "compose", "bundles")} {
		if err := privateDir(dir); err != nil {
			return "", err
		}
	}
	parent := filepath.Join(root, "compose", "bundles")
	bundle := filepath.Join(parent, digest(files))
	if _, err := os.Lstat(bundle); err == nil {
		return bundle, verifyBundle(bundle)
	} else if !os.IsNotExist(err) {
		return "", err
	}
	temporary, err := os.MkdirTemp(parent, ".bundle-")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(temporary)
	if err := writeFiles(temporary, files); err != nil {
		return "", err
	}
	if err := os.Rename(temporary, bundle); err != nil {
		// Another cast may have published the same content concurrently.
		if _, statErr := os.Lstat(bundle); statErr != nil {
			return "", err
		}
	}
	return bundle, verifyBundle(bundle)
}

// Saved checks containment, privacy, file types and content identity before any
// installed lifecycle code is executed. New binary defaults never replace it.
func (p *Project) Saved() (string, error) {
	root, err := canonicalPath(p.app.State.StateDir)
	if err != nil {
		return "", err
	}
	path := filepath.Join(root, pointerName)
	if err := privateFile(path); err != nil {
		return "", fmt.Errorf("saved Compose project unavailable; use its original configuration to retry cast: %w", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	var saved savedProject
	if err := json.Unmarshal(data, &saved); err != nil || saved.Schema != 1 || (saved.Status != "pending" && saved.Status != "ready") || !filepath.IsAbs(saved.Bundle) {
		return "", fmt.Errorf("invalid saved Compose project; restore its original private project pointer")
	}
	bundle := filepath.Clean(saved.Bundle)
	if filepath.Dir(bundle) != filepath.Join(root, "compose", "bundles") {
		return "", fmt.Errorf("saved Compose bundle is outside this namespace's private bundles")
	}
	for _, dir := range []string{filepath.Join(root, "compose"), filepath.Dir(bundle)} {
		info, err := os.Lstat(dir)
		if err != nil || !info.IsDir() || info.Mode().Perm()&0077 != 0 {
			return "", fmt.Errorf("saved Compose bundle directory is not a private real directory")
		}
	}
	if err := verifyBundle(bundle); err != nil {
		return "", err
	}
	data, err = os.ReadFile(filepath.Join(bundle, ".cli-vars.json"))
	if err != nil {
		return "", err
	}
	var config configuration
	if err := json.Unmarshal(data, &config); err != nil || config.Schema != 1 || config.Namespace != p.app.BP.Metadata.Namespace || config.StateDir != root {
		return "", fmt.Errorf("saved Compose bundle belongs to a different deployment")
	}
	return bundle, nil
}

func (p *Project) Run(action string, opts Options) error {
	var bundle string
	var err error
	switch action {
	case "up":
		bundle, err = p.Publish()
	case "check":
		var files []generate.File
		files, err = p.files()
		if err == nil {
			bundle, err = os.MkdirTemp("", "aiv-compose-check-")
			if err == nil {
				defer os.RemoveAll(bundle)
				err = writeFiles(bundle, files)
			}
		}
	case "status", "logs", "down":
		bundle, err = p.Saved()
	default:
		return fmt.Errorf("unsupported Compose action")
	}
	if err != nil {
		return err
	}
	if action == "status" || action == "logs" {
		if err := RunLifecycle(p.app.Ctx, p.app.Docker.Bin, filepath.Join(bundle, "compose.yaml"), os.DevNull, "check", false); err != nil {
			return err
		}
		return p.native(bundle, action, opts)
	}
	if action != "check" {
		fmt.Printf("Compose project: %s\n", bundle)
		fmt.Printf("Native status: %s compose --project-name %s --file %s --env-file %s ps --all\n", shellQuote(p.app.Docker.Bin), shellQuote(p.app.BP.Metadata.Namespace), shellQuote(filepath.Join(bundle, "compose.yaml")), shellQuote(os.DevNull))
	}
	return RunLifecycle(p.app.Ctx, p.app.Docker.Bin, filepath.Join(bundle, "compose.yaml"), os.DevNull, action, opts.Purge)
}

// RunFile operates an explicitly selected exported project. Its YAML and env
// file own configuration; no blueprint state or target is inferred or adopted.
func RunFile(ctx context.Context, docker, file, envFile, action string, opts Options) error {
	composeFile, err := filepath.Abs(file)
	if err != nil {
		return err
	}
	privateEnv, err := filepath.Abs(envFile)
	if err != nil {
		return err
	}
	checkAction := action
	if action == "status" || action == "logs" {
		checkAction = "check"
	}
	if err := RunLifecycle(ctx, docker, composeFile, privateEnv, checkAction, opts.Purge); err != nil {
		return err
	}
	if action == "status" || action == "logs" {
		return nativeCompose(ctx, docker, composeFile, privateEnv, "", action, opts)
	}
	return nil
}

// Observation uses native Compose after Go verifies the exact saved bundle.
// There is no Python requirement and ambient Compose selectors cannot retarget it.
func (p *Project) native(bundle, action string, opts Options) error {
	return nativeCompose(p.app.Ctx, p.app.Docker.Bin, filepath.Join(bundle, "compose.yaml"), os.DevNull, p.app.BP.Metadata.Namespace, action, opts)
}

func nativeCompose(ctx context.Context, docker, file, envFile, namespace, action string, opts Options) error {
	args := []string{"compose", "--file", file, "--env-file", envFile}
	if namespace != "" {
		args = append(args, "--project-name", namespace)
	}
	if action == "status" {
		args = append(args, "ps", "--all")
	} else {
		args = append(args, "logs", "--tail", strconv.Itoa(opts.Tail))
		if opts.Follow {
			args = append(args, "--follow")
		}
		if opts.Component != "" {
			args = append(args, components.Canonical(opts.Component))
		}
	}
	quoted := make([]string, 0, len(args)+1)
	for _, value := range append([]string{docker}, args...) {
		quoted = append(quoted, shellQuote(value))
	}
	fmt.Printf("Compose project: %s\nNative command: %s\n", filepath.Dir(file), strings.Join(quoted, " "))
	cmd := exec.CommandContext(ctx, docker, args...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	cmd.Env = []string{}
	for _, value := range os.Environ() {
		if !strings.HasPrefix(value, "COMPOSE_") {
			cmd.Env = append(cmd.Env, value)
		}
	}
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("native Compose command failed: %w", err)
	}
	return nil
}

func digest(files []generate.File) string {
	ordered := append([]generate.File(nil), files...)
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].Path < ordered[j].Path })
	hash := sha256.New()
	for _, file := range ordered {
		_ = binary.Write(hash, binary.BigEndian, uint64(len(file.Path)))
		_, _ = hash.Write([]byte(file.Path))
		_ = binary.Write(hash, binary.BigEndian, uint64(len(file.Content)))
		_, _ = hash.Write(file.Content)
	}
	return hex.EncodeToString(hash.Sum(nil))
}

func verifyBundle(bundle string) error {
	name := filepath.Base(bundle)
	if decoded, err := hex.DecodeString(name); err != nil || len(decoded) != sha256.Size || strings.ToLower(name) != name {
		return fmt.Errorf("invalid Compose bundle content identity")
	}
	var files []generate.File
	err := filepath.WalkDir(bundle, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0077 != 0 {
			return fmt.Errorf("Compose bundle must contain only private real files/directories")
		}
		if info.IsDir() {
			return nil
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("invalid Compose bundle file type")
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		relative, err := filepath.Rel(bundle, path)
		if err != nil {
			return err
		}
		files = append(files, generate.File{Path: filepath.ToSlash(relative), Content: data})
		return nil
	})
	if err != nil {
		return err
	}
	if digest(files) != name {
		return fmt.Errorf("Compose bundle integrity check failed; restore the original immutable bundle")
	}
	return nil
}

func writeFiles(root string, files []generate.File) error {
	for _, file := range files {
		path := filepath.Join(root, filepath.FromSlash(file.Path))
		relative, err := filepath.Rel(root, path)
		if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			return fmt.Errorf("invalid generated Compose path")
		}
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			return err
		}
		mode := os.FileMode(0600)
		if strings.HasSuffix(file.Path, ".sh") {
			mode = 0700
		}
		if err := os.WriteFile(path, file.Content, mode); err != nil {
			return err
		}
	}
	return nil
}

func privateDir(path string) error {
	if err := os.MkdirAll(path, 0700); err != nil {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() {
		return fmt.Errorf("Compose bundle directory must be a real directory")
	}
	return os.Chmod(path, 0700)
}

func privateFile(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
		return fmt.Errorf("expected a private regular file")
	}
	return nil
}

// Canonicalize existing ancestors too, including macOS /var and /tmp aliases.
func canonicalPath(path string) (string, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	resolved, err := filepath.EvalSymlinks(absolute)
	if err == nil {
		return resolved, nil
	}
	if !os.IsNotExist(err) {
		return "", err
	}
	parent := filepath.Dir(absolute)
	if parent == absolute {
		return "", err
	}
	resolved, err = canonicalPath(parent)
	if err != nil {
		return "", err
	}
	return filepath.Join(resolved, filepath.Base(absolute)), nil
}

func shellQuote(value string) string { return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'" }
