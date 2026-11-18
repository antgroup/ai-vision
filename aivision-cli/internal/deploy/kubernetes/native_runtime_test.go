package kubernetes

import (
	"context"
	"fmt"
	"io/fs"
	"strings"
	"testing"

	manifests "github.com/antgroup/ai-vision/aivision-cli/deploy/kubernetes"
)

func TestNativeRuntimeRendersSourceFreeKubernetesLifecycle(t *testing.T) {
	for _, native := range []bool{true, false} {
		t.Run(fmt.Sprint(native), func(t *testing.T) {
			a, err := New(context.Background(), Options{Runner: newCluster(), StateRoot: t.TempDir(), Overrides: map[string]string{"NATIVE_PYTHON_RUNTIME": fmt.Sprint(native)}})
			if err != nil {
				t.Fatal(err)
			}
			files, err := fs.Glob(manifests.Files, "*.yaml")
			if err != nil {
				t.Fatal(err)
			}
			for _, file := range files {
				data, err := a.render(file)
				if err != nil {
					t.Fatal(err)
				}
				docs, err := decodeDocuments(data)
				if err != nil {
					t.Fatalf("%s: %v", file, err)
				}
				text := string(data)
				if native && (strings.Contains(text, "python") || strings.Contains(text, "manage.py") || strings.Contains(text, ".py:")) {
					t.Errorf("native Kubernetes manifest contains a source program: %s", file)
				}
				if file == "database-init.yaml" {
					container := list(docs[0], "spec", "template", "spec", "containers")[0]
					command := list(container, "command")
					if native && (len(command) != 1 || command[0] != "/app/database-init" || len(list(container, "args")) != 0) {
						t.Fatal("native initialization must execute the packaged helper without an inline program")
					}
					if !native && !strings.Contains(text, "import os, time, pymysql") {
						t.Fatal("legacy initialization was lost")
					}
				}
			}
			command := strings.Join(a.backendManageArgs("signoz_integration_status", "--print-org", "--require"), " ")
			if native && (!strings.Contains(command, "-- /app/manage ") || strings.Contains(command, "python")) {
				t.Fatalf("native org discovery bypasses packaged manage: %s", command)
			}
			if !native && !strings.Contains(command, "-- python manage.py ") {
				t.Fatalf("legacy org discovery changed: %s", command)
			}
		})
	}
}

func TestNativeRuntimeModeValidation(t *testing.T) {
	values := defaults()
	values["NATIVE_PYTHON_RUNTIME"] = "sometimes"
	if err := (Config{Namespace: "aivision", Values: values}).validate(); err == nil || !strings.Contains(err.Error(), "NATIVE_PYTHON_RUNTIME") {
		t.Fatalf("expected runtime mode validation: %v", err)
	}
}
