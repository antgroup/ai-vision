package sandboxbootstrap

import (
	"context"
	"errors"
	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
	"testing"
)

func TestEnvironmentUsesActualCapabilitiesAndDefersOtherKernel(t *testing.T) {
	for _, tt := range []struct {
		name, version, kernel, local string
		abi                          int
		linux                        bool
		code                         string
		deferred                     bool
	}{
		{"old docker", "26.1.3", "5.10.1", "5.10.1", 0, true, "docker_version_unsupported", false},
		{"missing landlock", "29.9.3", "5.10.1", "5.10.1", 0, true, "landlock_unavailable", false},
		{"old abi", "28.0.0", "6.1.0", "6.1.0", 2, true, "landlock_abi_unsupported", false},
		{"backport", "28.0.0", "5.10.1", "5.10.1", 3, true, "", false},
		{"other kernel", "29.9.3", "6.8.0", "5.10.1", 0, true, "", true},
		{"darwin host", "28.0.0", "6.8.0", "", 0, false, "", true},
		{"unknown engine", "", "6.8.0", "6.8.0", 4, true, "environment_probe_failed", false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			deferred, err := validateEnvironment(daemonInfo{ServerVersion: tt.version, KernelVersion: tt.kernel}, localEnvironment{Linux: tt.linux, Kernel: tt.local, ABI: tt.abi})
			if deferred != tt.deferred {
				t.Fatalf("deferred=%v", deferred)
			}
			if tt.code == "" {
				if err != nil {
					t.Fatal(err)
				}
				return
			}
			var diagnostic *EnvironmentError
			if !errors.As(err, &diagnostic) || diagnostic.Diagnostic.Code != tt.code {
				t.Fatalf("wrong safe diagnostic: %#v %v", diagnostic, err)
			}
		})
	}
}

func TestEnvironmentFailureClassificationPreservesCancellation(t *testing.T) {
	for _, tt := range []struct{ stage, code string }{
		{"images", "image_download_failed"}, {"configuration", "configuration_failed"},
		{"gateway", "gateway_start_failed"}, {"plugin", "plugin_start_failed"},
		{"readiness", "provider_not_ready"}, {"registration", "registration_failed"},
	} {
		if got := failureDiagnostic(errors.New("SECRET /private/path"), tt.stage, nil); got.Code != tt.code {
			t.Fatalf("%s: %#v", tt.stage, got)
		}
	}
	if got := failureDiagnostic(diagnosticError("landlock_unavailable", "0", "3"), "readiness", context.DeadlineExceeded); got.Code != "timeout" {
		t.Fatal(got)
	}
	if got := failureDiagnostic(errors.New("SECRET"), "images", context.Canceled); got.Code != "canceled" {
		t.Fatal(got)
	}
}

func TestRuntimeEnvironmentCannotUseLegacyMetadataAsProof(t *testing.T) {
	for _, health := range []*sandboxplugin.Health{nil, {}, {Environment: &sandboxplugin.EnvironmentHealth{Status: "passed", DockerVersion: "29.0"}}} {
		if err := runtimeEnvironmentError(health); err == nil {
			t.Fatal("unqualified legacy metadata accepted")
		}
	}
	abi := 4
	if err := runtimeEnvironmentError(&sandboxplugin.Health{Environment: &sandboxplugin.EnvironmentHealth{Status: "passed", DockerVersion: "29.9.3", LandlockABI: &abi}}); err != nil {
		t.Fatal(err)
	}
}
