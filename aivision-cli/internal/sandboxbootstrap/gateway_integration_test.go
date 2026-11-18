package sandboxbootstrap

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
)

// The actual pinned gateway is the schema consumer. This opt-in Docker check
// catches misplaced fields that typed CLI fixtures cannot validate.
func TestGeneratedGatewayConfigurationPassesPinnedPreflight(t *testing.T) {
	image := os.Getenv("AIV_TEST_OPENSHELL_GATEWAY_IMAGE")
	if image == "" {
		t.Skip("set AIV_TEST_OPENSHELL_GATEWAY_IMAGE to the qualified 0.1.6 digest for Docker schema acceptance")
	}
	if !digestRef.MatchString(image) {
		t.Fatal("the gateway integration image must be digest-pinned")
	}
	directory, err := os.MkdirTemp("", "aiv-gateway-preflight-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(directory)
	plan := validPlan(t)
	plan.Target.GatewayName = "aiv-gateway-preflight-" + filepath.Base(directory)[len("aiv-gateway-preflight-"):]
	state := &ownedState{Dir: directory, GatewayPort: 29999}
	if err = privateDirectory(filepath.Join(directory, "gateway-state")); err != nil {
		t.Fatal(err)
	}
	images := plan.Catalog.Images["linux/arm64"]
	images.Gateway = image
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	if err = ensureTLS(ctx, sandboxplugin.NativeDocker{}, plan, state, images); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "gateway.toml")
	if err = os.WriteFile(path, state.gatewayConfig(plan, images), 0600); err != nil {
		t.Fatal(err)
	}
	data := filepath.Join(directory, "gateway-state")
	command := exec.CommandContext(ctx, "docker", "run", "--rm", "--network", "none", "--read-only", "--user", fmt.Sprintf("%d:%d", os.Getuid(), os.Getgid()), "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true", "--mount", "type=bind,source="+directory+",target="+directory+",readonly", "--env", "HOME="+data, "--env", "XDG_DATA_HOME="+data, "--env", "OPENSHELL_LOCAL_TLS_DIR="+filepath.Join(data, "tls"), "--env", "OPENSHELL_ENABLE_MTLS_AUTH=true", image, "config", "preflight", "--path", path)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("pinned gateway rejected generated configuration: %v\n%s", err, output)
	}
}
