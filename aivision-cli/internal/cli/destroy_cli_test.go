package cli

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
)

func captureCLIStderr(t *testing.T, stdin string, fn func() error) (string, error) {
	t.Helper()
	oldIn, oldErr := os.Stdin, os.Stderr
	inR, inW, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	outR, outW, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stdin, os.Stderr = inR, outW
	defer func() {
		os.Stdin, os.Stderr = oldIn, oldErr
		_ = inR.Close()
		_ = outR.Close()
	}()
	if _, err := io.WriteString(inW, stdin); err != nil {
		t.Fatal(err)
	}
	_ = inW.Close()
	runErr := fn()
	_ = outW.Close()
	out, err := io.ReadAll(outR)
	if err != nil {
		t.Fatal(err)
	}
	return string(out), runErr
}

func TestDestroyRefusesUnsafeNamespaceBeforeTeardown(t *testing.T) {
	dir := t.TempDir()
	marker := filepath.Join(dir, "teardown-ran")
	bpPath := filepath.Join(dir, "blueprint.yaml")
	content := fmt.Sprintf(`metadata: {name: ns-test, namespace: aivision}
config:
  workdir: %s
  sourceRoot: %s
components:
  - name: demo
    kind: process
    run: "true"
    teardown:
      - shell: "touch %s"
`, dir, dir, marker)
	if err := os.WriteFile(bpPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := cmdDestroy([]string{"--file", bpPath, "--workdir", dir, "--namespace", "../victim", "--purge", "-y"}); err == nil {
		t.Fatal("destroy accepted a namespace outside workdir")
	}
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Fatalf("teardown ran before the namespace was rejected: %v", err)
	}
}

func TestDestroyConfirmationMatchesTarget(t *testing.T) {
	cases := []struct {
		name      string
		target    string
		wantState bool
	}{
		{name: "docker-stops-state", target: "docker", wantState: true},
		{name: "compose-retains-state", target: "compose", wantState: false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			dir := t.TempDir()
			out, err := captureCLIStderr(t, "n\n", func() error {
				args := []string{
					"--file", "../../blueprints/aivision.yaml",
					"--workdir", dir,
					"--namespace", "compose-test",
					"--purge",
				}
				if tc.target != "docker" {
					args = append(args, "--target", tc.target)
				}
				return cmdDestroy(args)
			})
			if err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(out, "Continue? [y/N]") {
				t.Fatalf("confirmation prompt missing: %q", out)
			}
			hasState := strings.Contains(out, "State directory to delete")
			if hasState != tc.wantState {
				t.Fatalf("state-directory line present=%v, want %v: %q", hasState, tc.wantState, out)
			}
			if tc.target == "compose" && !strings.Contains(out, "named volumes") {
				t.Fatalf("Compose volume warning missing: %q", out)
			}
		})
	}
}

func TestDestroyExternalDatabaseWarnsAboutDockerNamedVolumes(t *testing.T) {
	dir := t.TempDir()
	bp := filepath.Join(dir, "blueprint.yaml")
	config := fmt.Sprintf(`metadata: {name: archive, namespace: demo}
config: {workdir: %q, sourceRoot: %q, vars: {OB_MANAGED: "false", OB_HOST: "db.example"}}
components:
  - name: archive
    kind: container
    image: fixture
    mounts: ["type=volume,source=archive,target=/archive"]
`, filepath.Join(dir, "work"), filepath.Join(dir, "src"))
	if err := os.WriteFile(bp, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
	out, err := captureCLIStderr(t, "n\n", func() error {
		return cmdDestroy([]string{"--file", bp, "--purge"})
	})
	if err != nil {
		t.Fatal(err)
	}
	warning, _, prompted := strings.Cut(out, "Continue?")
	if !prompted || !strings.Contains(warning, "named volumes") || !strings.Contains(warning, "external OceanBase at db.example") {
		t.Fatalf("expected the Docker volume warning and external database scope before confirmation: %q", out)
	}
}

func TestDestroyPromptListsRecordedRepos(t *testing.T) {
	dir := t.TempDir()
	src := filepath.Join(dir, "src")
	repo := forgeTestCheckout(t, dir, src, "repo-prompt", "backend")
	stale := forgeTestCheckout(t, dir, src, "repo-prompt", "stale")
	if err := runtime.NewState(dir, "repo-prompt").RecordManagedRepo(stale, "different-id"); err != nil {
		t.Fatal(err)
	}
	out, err := captureCLIStderr(t, "n\n", func() error {
		return cmdDestroy([]string{
			"--file", "../../blueprints/aivision.yaml",
			"--workdir", dir,
			"--source-root", filepath.Join(dir, "src"),
			"--namespace", "repo-prompt",
			"--purge-repos",
		})
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, repo) {
		t.Fatalf("recorded repo missing from confirmation: %q", out)
	}
	if strings.Contains(out, stale) {
		t.Fatalf("stale record was listed for deletion: %q", out)
	}
}

// Exercise the same Git clone, ownership marker and record registration as a
// source deployment. The origin is local so the test also runs offline.
func forgeTestCheckout(t *testing.T, work, sourceRoot, namespace, name string) string {
	t.Helper()
	t.Setenv("GIT_CONFIG_GLOBAL", os.DevNull)
	t.Setenv("GIT_TEMPLATE_DIR", t.TempDir())
	origin := t.TempDir()
	for _, args := range [][]string{
		{"-c", "init.defaultBranch=main", "init", "--quiet", origin},
		{"-C", origin, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
			"-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "fixture"},
	} {
		if out, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("prepare local origin: %v\n%s", err, out)
		}
	}
	config := fmt.Sprintf(`metadata: {name: fixture, namespace: %q}
config: {workdir: %q, sourceRoot: %q}
components:
  - {name: app, kind: process, run: "true", repo: {path: %q, url: %q, branch: main}}
`, namespace, work, sourceRoot, name, origin)
	bp := filepath.Join(t.TempDir(), "forge.yaml")
	if err := os.WriteFile(bp, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := cmdSimple("forge", []string{"--file", bp}, (*runtime.App).Forge); err != nil {
		t.Fatal(err)
	}
	return filepath.Join(sourceRoot, name)
}

func TestDestroyPurgeReposThroughCLI(t *testing.T) {
	dir := t.TempDir()
	sourceRoot := filepath.Join(dir, "src")
	cloned := forgeTestCheckout(t, dir, sourceRoot, "aivision", "cloned")
	existing := filepath.Join(sourceRoot, "existing")
	if err := os.MkdirAll(filepath.Join(existing, ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(existing, "file"), []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	bpPath := filepath.Join(dir, "blueprint.yaml")
	content := fmt.Sprintf(`metadata: {name: cli-destroy, namespace: aivision}
config:
  workdir: %s
  sourceRoot: %s
components:
  - {name: cloned, kind: process, run: "true", repo: {path: cloned}}
  - {name: existing, kind: process, run: "true", repo: {path: existing}}
`, dir, sourceRoot)
	if err := os.WriteFile(bpPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := cmdDestroy([]string{"--file", bpPath, "--workdir", dir, "--namespace", "aivision", "--purge-repos", "-y"}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(cloned); !os.IsNotExist(err) {
		t.Fatalf("aiv clone was not removed: %v", err)
	}
	if _, err := os.Stat(filepath.Join(existing, "file")); err != nil {
		t.Fatalf("pre-existing checkout was removed: %v", err)
	}
}

func TestDestroyRejectsSourceInsideStateBeforeTeardown(t *testing.T) {
	for _, tc := range []struct {
		name       string
		sourceRoot string
		repoPath   string
		flag       string
		yes        bool
	}{
		{"source-root", "demo/sources", "checkout", "--purge", true},
		{"source-root-is-state", "demo", "checkout", "--purge-repos", true},
		{"checkout", ".", "demo/checkout", "--purge", true},
		{"interactive", "demo/sources", "checkout", "--purge-repos", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			work := t.TempDir()
			sourceRoot := filepath.Join(work, tc.sourceRoot)
			if err := os.MkdirAll(filepath.Join(sourceRoot, tc.repoPath, ".git"), 0o755); err != nil {
				t.Fatal(err)
			}
			teardown := filepath.Join(work, "teardown-ran")
			config := fmt.Sprintf(`metadata: {name: layout, namespace: demo}
config: {workdir: %q, sourceRoot: %q}
components:
  - name: app
    kind: process
    run: "true"
    repo: {path: %q}
    teardown:
      - shell: "touch %s"
`, work, sourceRoot, tc.repoPath, teardown)
			bp := filepath.Join(work, "blueprint.yaml")
			if err := os.WriteFile(bp, []byte(config), 0o600); err != nil {
				t.Fatal(err)
			}
			args := []string{"--file", bp, tc.flag}
			if tc.yes {
				args = append(args, "-y")
			}
			output, err := captureCLIStderr(t, "n\n", func() error { return cmdDestroy(args) })
			if err == nil || !strings.Contains(err.Error(), "state directory") {
				t.Fatalf("overlapping purge should identify the state directory, got %v", err)
			}
			if strings.Contains(output, "Continue?") {
				t.Fatal("asked for confirmation of an unsafe layout")
			}
			if _, err := os.Stat(teardown); !os.IsNotExist(err) {
				t.Fatalf("unexpected teardown marker: %v", err)
			}
			if _, err := os.Stat(filepath.Join(sourceRoot, tc.repoPath, ".git")); err != nil {
				t.Fatalf("existing checkout was not preserved: %v", err)
			}
		})
	}
}

func TestDestroyKeepsCheckoutRegisteredAfterEmptyConfirmation(t *testing.T) {
	dir := t.TempDir()
	sourceRoot := filepath.Join(dir, "src")
	state := runtime.NewState(dir, "aivision")
	bpPath := filepath.Join(dir, "blueprint.yaml")
	content := fmt.Sprintf(`metadata: {name: cli-destroy, namespace: aivision}
config:
  workdir: %s
  sourceRoot: %s
components:
  - {name: demo, kind: process, run: "true", repo: {path: demo}}
`, dir, sourceRoot)
	if err := os.WriteFile(bpPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	in, answer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer in.Close()
	defer answer.Close()
	out, prompt, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer out.Close()
	defer prompt.Close()
	if err := out.SetReadDeadline(time.Now().Add(5 * time.Second)); err != nil {
		t.Fatal(err)
	}

	oldIn, oldErr := os.Stdin, os.Stderr
	os.Stdin, os.Stderr = in, prompt
	done := make(chan struct{})
	var destroyErr error
	go func() {
		defer close(done)
		defer prompt.Close()
		destroyErr = cmdDestroy([]string{"--file", bpPath, "--purge-repos"})
	}()
	defer func() {
		_ = answer.Close()
		<-done
		os.Stdin, os.Stderr = oldIn, oldErr
	}()
	preview, err := bufio.NewReader(out).ReadString(']')
	if err != nil || !strings.Contains(preview, "no verified aiv-cloned checkouts to delete") {
		t.Fatalf("expected an empty confirmation, got %q: %v", preview, err)
	}

	checkout := forgeTestCheckout(t, dir, sourceRoot, "aivision", "demo")
	if _, err := io.WriteString(answer, "y\n"); err != nil {
		t.Fatal(err)
	}
	<-done
	if destroyErr != nil {
		t.Fatal(destroyErr)
	}
	if _, err := os.Stat(filepath.Join(checkout, ".git")); err != nil {
		t.Fatalf("checkout registered after the prompt was not preserved: %v", err)
	}
	repos, err := state.ManagedRepos()
	if err != nil || len(repos) != 1 || repos[0].Path != checkout || repos[0].ID == "" {
		t.Fatalf("new checkout record = %v, %v", repos, err)
	}
}

func TestDestroyRetainsWorkdirInsideManagedCheckout(t *testing.T) {
	src := t.TempDir()
	work := filepath.Join(src, "app", "work")
	checkout := forgeTestCheckout(t, work, src, "dev", "app")
	state := runtime.NewState(work, "dev")
	if err := state.EnsureDirs(); err != nil {
		t.Fatal(err)
	}
	config := fmt.Sprintf(`metadata: {name: layout, namespace: dev}
config: {workdir: %q, sourceRoot: %q}
components:
  - {name: app, kind: process, run: "true", repo: {path: app}}
`, work, src)
	bp := filepath.Join(t.TempDir(), "blueprint.yaml")
	if err := os.WriteFile(bp, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := cmdDestroy([]string{"--file", bp, "--purge-repos", "-y"}); err == nil ||
		!strings.Contains(err.Error(), "contains the deployment state directory") {
		t.Fatalf("expected a workdir overlap error, got %v", err)
	}
	repos, err := state.ManagedRepos()
	if err != nil || len(repos) != 1 || repos[0].Path != checkout || repos[0].Pending {
		t.Fatalf("checkout and registry should remain usable: %v, %v", repos, err)
	}
}

func TestDestroyPurgeIgnoresUnavailableRecordStorage(t *testing.T) {
	dir := t.TempDir()
	marker := filepath.Join(dir, "teardown-ran")
	state := runtime.NewState(dir, "aivision")
	if err := state.EnsureDirs(); err != nil {
		t.Fatal(err)
	}
	blocker := filepath.Join(dir, ".aivision-repos")
	if err := os.WriteFile(blocker, []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	bpPath := filepath.Join(dir, "blueprint.yaml")
	content := fmt.Sprintf(`metadata: {name: record-storage, namespace: aivision}
config:
  workdir: %s
  sourceRoot: %s
components:
  - name: demo
    kind: process
    run: "true"
    teardown:
      - shell: "touch %s"
`, dir, dir, marker)
	if err := os.WriteFile(bpPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := cmdDestroy([]string{"--file", bpPath, "--workdir", dir, "--namespace", "aivision", "--purge", "-y"}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatalf("teardown did not run: %v", err)
	}
	if _, err := os.Stat(state.Root); !os.IsNotExist(err) {
		t.Fatalf("state directory survived the purge: %v", err)
	}
	if data, err := os.ReadFile(blocker); err != nil || string(data) != "keep" {
		t.Fatalf("unrelated record path changed: %q, %v", data, err)
	}
}

func TestDestroyStatePreflightRefusesBeforeVolumeRemoval(t *testing.T) {
	workdir := t.TempDir()
	sourceRoot := t.TempDir()
	outside := t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "keep"), []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(workdir, "aivision")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}

	capture := filepath.Join(t.TempDir(), "docker-args")
	fakeDocker := filepath.Join(t.TempDir(), "docker")
	script := "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$AIV_DOCKER_CAPTURE\"\ncase \"$1\" in\n  container) echo \"No such container: $3\" >&2 ;;\n  volume) echo \"No such volume: $3\" >&2 ;;\nesac\nexit 1\n"
	if err := os.WriteFile(fakeDocker, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DOCKER_BIN", fakeDocker)
	t.Setenv("AIV_DOCKER_CAPTURE", capture)

	bpPath := filepath.Join(t.TempDir(), "blueprint.yaml")
	content := fmt.Sprintf(`metadata: {name: vol-test, namespace: aivision}
config:
  workdir: %s
  sourceRoot: %s
  dockerNetwork: test
components:
  - {name: demo, kind: container, image: busybox, volumes: ["aivision-data:/data"]}
`, workdir, sourceRoot)
	if err := os.WriteFile(bpPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := cmdDestroy([]string{"--file", bpPath, "--workdir", workdir, "--namespace", "aivision", "--purge", "-y"}); err == nil {
		t.Fatal("destroy accepted a state root that resolves outside workdir")
	}
	args, err := os.ReadFile(capture)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(args), "volume rm") {
		t.Fatalf("volume removal ran despite the state preflight failure: %q", args)
	}
	if _, err := os.Stat(filepath.Join(outside, "keep")); err != nil {
		t.Fatalf("state target outside workdir was modified: %v", err)
	}
}
