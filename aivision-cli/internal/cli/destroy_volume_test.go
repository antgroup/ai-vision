package cli

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
)

func TestDestroyRetriesFailedVolumePurge(t *testing.T) {
	if bp := os.Getenv("AIV_TEST_VOLUME_PURGE_BLUEPRINT"); bp != "" {
		os.Args = []string{"aiv", "destroy", "--file", bp, "--purge-repos", "-y"}
		Run("")
		return
	}

	work, src, fixture := t.TempDir(), t.TempDir(), t.TempDir()
	checkout := forgeTestCheckout(t, work, src, "demo", "checkout")
	state := runtime.NewState(work, "demo")
	if err := state.EnsureDirs(); err != nil {
		t.Fatal(err)
	}
	stateFile := filepath.Join(state.StateDir, "recovery-data")
	if err := os.WriteFile(stateFile, []byte("retain until purge succeeds"), 0o600); err != nil {
		t.Fatal(err)
	}
	bp := filepath.Join(fixture, "blueprint.yaml")
	config := fmt.Sprintf(`metadata: {name: volume-test, namespace: demo}
config: {workdir: %q, sourceRoot: %q}
components:
  - name: first
    kind: container
    image: fixture
    volumes: ["shared:/data", "./config:/config:ro", "/anonymous"]
  - name: second
    kind: container
    image: fixture
    dependsOn: [first]
    volumes: ["shared:/data:ro"]
    mounts: ["type=volume,source=archive,target=/archive", "type=bind,src=./logs,target=/logs"]
    teardown:
      - shell: 'if test -f "$AIV_TEST_VOLUME_FIXTURE/transfer"; then mv "$AIV_TEST_VOLUME_FIXTURE/transfer" "$AIV_TEST_VOLUME_FIXTURE/compose-owned"; fi'
`, work, src)
	if err := os.WriteFile(bp, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
	// Docker refuses to delete a volume referenced by any remaining container.
	// The external holder survives the first destroy and is released before retry.
	for _, resource := range []string{"demo-first", "demo-second", "external-holder", "shared", "archive"} {
		if err := os.WriteFile(filepath.Join(fixture, resource), nil, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	docker := filepath.Join(fixture, "docker")
	const script = `#!/bin/sh
set -eu
cd "$AIV_TEST_VOLUME_FIXTURE"
case "$1 $2" in
  "container inspect")
    if [ ! -f "$3" ]; then echo "No such container: $3" >&2; exit 1; fi
    printf '[{"Id":"%s","Config":{"Labels":{}}}]\n' "$3" ;;
  "volume inspect")
    if [ ! -f "$3" ]; then echo "No such volume: $3" >&2; exit 1; fi
    labels='{}'
    if [ "$3" = shared ] && [ -f compose-owned ]; then labels='{"com.docker.compose.project":"other"}'; fi
    printf '[{"Name":"%s","Labels":%s}]\n' "$3" "$labels" ;;
  "inspect demo-"*) test -f "$2" ;;
  "rm -f") rm -f "$3" ;;
  "volume rm")
    shift 2
    force=false
    for name do
      case "$name" in
        -f|--force) force=true; continue ;;
        --) continue ;;
        shared|archive) ;;
        *) echo "unexpected $name" >> calls; exit 1 ;;
      esac
      echo "$name" >> calls
      if [ -f demo-first ] || [ -f demo-second ]; then
        echo "volume deletion before container removal" >> calls; exit 1
      fi
      if [ "$name" = shared ] && [ -f external-holder ]; then
        echo "volume is in use" >&2; exit 1
      fi
      if [ ! -f "$name" ] && [ "$force" = false ]; then
        echo "No such volume: $name" >&2; exit 1
      fi
      rm -f "$name"
    done ;;
  *) echo "unexpected Docker call: $*" >&2; exit 1 ;;
esac
`
	if err := os.WriteFile(docker, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DOCKER_BIN", docker)
	t.Setenv("AIV_TEST_VOLUME_FIXTURE", fixture)
	t.Setenv("AIV_TEST_VOLUME_PURGE_BLUEPRINT", bp)
	run := func() ([]byte, error) {
		return exec.Command(os.Args[0], "-test.run=^TestDestroyRetriesFailedVolumePurge$").CombinedOutput()
	}
	assertRecoverable := func() {
		t.Helper()
		if data, err := os.ReadFile(stateFile); err != nil || string(data) != "retain until purge succeeds" {
			t.Errorf("failed purge changed deployment state: %q, %v", data, err)
		}
		records, err := state.ManagedRepos()
		if err != nil || len(records) != 1 || records[0].Path != checkout || records[0].Pending {
			t.Errorf("failed purge changed checkout registration: %v, %v", records, err)
		}
		if _, err := os.Stat(filepath.Join(checkout, ".git")); err != nil {
			t.Errorf("failed purge changed checkout: %v", err)
		}
	}
	out, err := run()
	exit, ok := err.(*exec.ExitError)
	_, diagnostic, _ := strings.Cut(string(out), "\naiv: ")
	if !ok || exit.ExitCode() != 1 || !strings.Contains(diagnostic, "shared") ||
		!strings.Contains(string(out), "volume is in use") || strings.Contains(string(out), "destroy complete") {
		t.Errorf("expected an unsuccessful purge identifying the held volume, got %v\n%s", err, out)
	}
	assertRecoverable()
	if err := os.Remove(filepath.Join(fixture, "external-holder")); err != nil {
		t.Fatal(err)
	}
	// A different deployment can acquire the name between preflight and purge.
	if err := os.WriteFile(filepath.Join(fixture, "transfer"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	out, err = run()
	exit, ok = err.(*exec.ExitError)
	if !ok || exit.ExitCode() != 1 || !strings.Contains(string(out), "shared is managed by Compose") {
		t.Errorf("expected refusal after the volume owner changed: %v\n%s", err, out)
	}
	if _, err := os.Stat(filepath.Join(fixture, "shared")); err != nil {
		t.Errorf("destroy removed the reassigned volume: %v", err)
	}
	assertRecoverable()
	if err := os.Remove(filepath.Join(fixture, "compose-owned")); err != nil {
		t.Fatal(err)
	}
	if out, err := run(); err != nil {
		t.Fatalf("retry after releasing the volume: %v\n%s", err, out)
	}
	for _, path := range []string{state.Root, checkout, filepath.Join(fixture, "shared"), filepath.Join(fixture, "archive")} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Errorf("successful retry should remove %s: %v", path, err)
		}
	}
	if records, err := state.ManagedRepos(); err != nil || len(records) != 0 {
		t.Errorf("checkout registration after successful retry: %v, %v", records, err)
	}
	calls, err := os.ReadFile(filepath.Join(fixture, "calls"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(calls), "unexpected") || strings.Contains(string(calls), "before container removal") ||
		strings.Count(string(calls), "shared\n") != 2 {
		t.Errorf("expected two removal attempts for the shared volume after teardown while owned, got\n%s", calls)
	}
}
