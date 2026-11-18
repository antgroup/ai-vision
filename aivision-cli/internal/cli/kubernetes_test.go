package cli

import (
	"flag"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
)

func TestDeploymentTargets(t *testing.T) {
	for _, args := range [][]string{nil, {"--target", "docker"}, {"--target", "kubernetes", "--context", "test"}} {
		fs := flag.NewFlagSet("cast", flag.ContinueOnError)
		g := registerGlobal(fs)
		registerTarget(fs, g)
		if err := fs.Parse(args); err != nil {
			t.Fatal(err)
		}
		if err := validateTarget("cast", fs, g); err != nil {
			t.Fatal(err)
		}
		if len(args) == 0 && g.target != "docker" {
			t.Fatal("default Docker behavior changed")
		}
	}
	for _, tc := range []struct {
		command string
		args    []string
	}{
		{"destroy", []string{"--target", "kubernetes", "--purge", "-y"}},
		{"redeploy", []string{"--target", "kubernetes"}},
		{"cast", []string{"--target", "invalid"}},
		{"cast", []string{"--context", "test"}},
		{"cast", []string{"--target", "kubernetes", "--file", "/missing/blueprint.yaml"}},
		{"cast", []string{"--target", "kubernetes", "--no-gauge"}},
		{"cast", []string{"--target", "kubernetes", "--ob-host", "db.example"}},
	} {
		t.Run(tc.command+strings.Join(tc.args, " "), func(t *testing.T) {
			var err error
			switch tc.command {
			case "destroy":
				err = cmdDestroy(tc.args)
			case "redeploy":
				err = cmdRedeploy(tc.args)
			default:
				err = cmdCast(tc.args)
			}
			if err == nil || strings.Contains(err.Error(), "no such file") || strings.Contains(err.Error(), "kubectl") {
				t.Fatalf("target must be rejected before loading/configuring runtimes: %v", err)
			}
		})
	}
	if err := cmdSimple("forge", []string{"--target", "kubernetes"}, func(*runtime.App) error { t.Fatal("Docker called"); return nil }); err == nil {
		t.Fatal("forge target accepted")
	}
}

func TestKubernetesStatusBypassesDockerBlueprint(t *testing.T) {
	dir := t.TempDir()
	// No Docker executable or valid blueprint is available. The Kubernetes
	// status command must still dispatch entirely through kubectl.
	script := `#!/bin/sh
case "$*" in
  *"namespace kube-system"*) printf '%s' '{"metadata":{"uid":"cli-test-cluster"}}' ;;
  *"get obcluster"*) printf '%s' '{"status":{"status":"running"}}' ;;
  *"get obtenant"*) printf '%s' '{"status":{"status":"running"}}' ;;
  *"get pods"*) printf '%s' '{"items":[{"metadata":{"labels":{"ref-obcluster":"oceanbase"}},"status":{"phase":"Running","conditions":[{"type":"Ready","status":"True"}]}}]}' ;;
  *"get deployment"*) printf '%s' '{"metadata":{"generation":1},"status":{"observedGeneration":1,"availableReplicas":1,"updatedReplicas":1}}' ;;
  *"get ingressclass"*) printf '%s' '{"spec":{"controller":"traefik.io/ingress-controller"}}' ;;
  *"get ingress"*) printf '%s' '{"spec":{"ingressClassName":"aivision"}}' ;;
  *) exit 9 ;;
esac
`
	if err := os.WriteFile(filepath.Join(dir, "kubectl"), []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)
	if err := cmdSimple("status", []string{"--target", "kubernetes", "--context", "test", "--reset-vars"}, func(*runtime.App) error { t.Fatal("Docker called"); return nil }); err != nil {
		t.Fatal(err)
	}
}

func TestKubernetesVariablePrecedence(t *testing.T) {
	path := filepath.Join(t.TempDir(), "vars.yaml")
	if err := os.WriteFile(path, []byte("IMAGE_TAG: from-file\nPUBLIC_HOST: from-file\n"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_VAR_IMAGE_TAG", "from-env")
	values, err := kubernetesOverrides(&globalOpts{varsFile: path, vars: varFlags{"IMAGE_TAG=from-flag"}})
	if err != nil {
		t.Fatal(err)
	}
	if values["IMAGE_TAG"] != "from-flag" || values["PUBLIC_HOST"] != "from-file" {
		t.Fatal("override precedence changed")
	}
}
