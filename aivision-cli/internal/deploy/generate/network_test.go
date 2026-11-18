package generate

import (
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
	"gopkg.in/yaml.v3"
)

func TestComposeUsesEffectiveDefaultNetwork(t *testing.T) {
	bp := &runtime.Blueprint{
		Metadata: runtime.Metadata{Namespace: "network-test"},
		Config:   runtime.Config{DockerNetwork: "default-net", Workdir: t.TempDir(), SourceRoot: t.TempDir(), Vars: map[string]string{"DOCKER_NETWORK": "custom-net"}},
		Components: []runtime.Component{
			{Name: "explicit", Kind: "container", Image: "fixture", Network: "${DOCKER_NETWORK}"},
			{Name: "default", Kind: "container", Image: "fixture"},
		},
	}
	app, err := runtime.NewApp(bp, runtime.NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	files, _, err := Render(app, Compose, Options{})
	if err != nil {
		t.Fatal(err)
	}
	var compose struct {
		Services map[string]struct{ Networks []string }
		Networks map[string]any
	}
	if err := yaml.Unmarshal(files[0].Content, &compose); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"explicit", "default"} {
		networks := compose.Services[name].Networks
		if len(networks) != 1 || networks[0] != "custom-net" {
			t.Errorf("service %s networks = %v, want custom-net", name, networks)
		}
	}
	if _, ok := compose.Networks["custom-net"]; !ok || len(compose.Networks) != 1 {
		t.Errorf("compose networks = %v, want only custom-net", compose.Networks)
	}
}

func TestDefaultBlueprintComposeUsesInternalGatewayPort(t *testing.T) {
	bp, err := blueprint.Load("../../../blueprints/aivision.yaml")
	if err != nil {
		t.Fatal(err)
	}
	bp.Config.Workdir = t.TempDir()
	bp.Config.Vars["GATEWAY_CONTAINER"] = "custom-gateway"
	bp.Config.Vars["GATEWAY_HTTP_PORT"] = "4328"
	bp.Config.Vars["PUBLIC_HOST"] = "vision.example.test"
	app, err := runtime.NewApp(bp, runtime.NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	files, _, err := Render(app, Compose, Options{})
	if err != nil {
		t.Fatal(err)
	}
	var compose struct {
		Services map[string]struct {
			Environment map[string]string
			Ports       []string
		}
	}
	if err := yaml.Unmarshal(files[0].Content, &compose); err != nil {
		t.Fatal(err)
	}
	env := compose.Services["backend"].Environment
	for key, want := range map[string]string{
		"AIVISION_OTLP_UPSTREAM_ENDPOINT": "http://custom-gateway:4318",
		"AIVISION_OTLP_INGEST_ENDPOINT":   "http://vision.example.test:4328",
	} {
		if got := env[key]; got != want {
			t.Errorf("%s = %q, want %q", key, got, want)
		}
	}
	want := "127.0.0.1:4328:4318"
	for _, port := range compose.Services["ingest-gateway"].Ports {
		if port == want {
			return
		}
	}
	t.Errorf("gateway ports = %v, want %q", compose.Services["ingest-gateway"].Ports, want)
}

func TestBlueprintComposePassesFixedNativeHistoryBounds(t *testing.T) {
	for _, file := range []string{"aivision.yaml", "aivision-source.yaml"} {
		for _, start := range []string{"", "2026-10-02T00:00:00Z"} {
			t.Run(file+"/"+start, func(t *testing.T) {
				bp, err := blueprint.Load("../../../blueprints/" + file)
				if err != nil {
					t.Fatal(err)
				}
				bp.Config.Workdir = t.TempDir()
				if start != "" {
					bp.Config.Vars["AIVISION_NATIVE_TRACE_HISTORY_START"] = start
				}
				app, err := runtime.NewApp(bp, runtime.NewLogger(false), false, false)
				if err != nil {
					t.Fatal(err)
				}
				files, _, err := Render(app, Compose, Options{})
				if err != nil {
					t.Fatal(err)
				}
				var compose struct {
					Services map[string]struct{ Environment map[string]string }
				}
				if err := yaml.Unmarshal(files[0].Content, &compose); err != nil {
					t.Fatal(err)
				}
				backendService := "backend"
				if file == "aivision-source.yaml" {
					backendService = "be"
				}
				for key, want := range map[string]string{
					"AIVISION_NATIVE_TRACE_HISTORY_START":       start,
					"AIVISION_NATIVE_TRACE_FUTURE_SKEW_SECONDS": "300",
					"AIVISION_NATIVE_TRACE_MAX_WINDOWS":         "120",
				} {
					got, exists := compose.Services[backendService].Environment[key]
					if !exists || got != want {
						t.Errorf("%s = %q (present=%v), want %q", key, got, exists, want)
					}
				}
			})
		}
	}
}

func TestBlueprintComposePassesInstalledReleaseMetadata(t *testing.T) {
	for _, file := range []string{"aivision.yaml", "aivision-source.yaml"} {
		for _, metadata := range []string{"", "eyJwcm9kdWN0X3ZlcnNpb24iOiIwLjEuMCJ9"} {
			t.Run(file+"/"+metadata, func(t *testing.T) {
				bp, err := blueprint.Load("../../../blueprints/" + file)
				if err != nil {
					t.Fatal(err)
				}
				bp.Config.Workdir = t.TempDir()
				if metadata != "" {
					bp.Config.Vars["AIVISION_RELEASE_MANIFEST_BASE64"] = metadata
				}
				app, err := runtime.NewApp(bp, runtime.NewLogger(false), false, false)
				if err != nil {
					t.Fatal(err)
				}
				files, _, err := Render(app, Compose, Options{})
				if err != nil {
					t.Fatal(err)
				}
				var compose struct {
					Services map[string]struct{ Environment map[string]string }
				}
				if err := yaml.Unmarshal(files[0].Content, &compose); err != nil {
					t.Fatal(err)
				}
				backendService := "backend"
				if file == "aivision-source.yaml" {
					backendService = "be"
				}
				got, present := compose.Services[backendService].Environment["AIVISION_RELEASE_MANIFEST_BASE64"]
				if !present || got != metadata {
					t.Fatalf("release metadata = %q (present=%v), want %q", got, present, metadata)
				}
			})
		}
	}
}
