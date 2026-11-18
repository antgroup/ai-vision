//go:build native_distribution

package kubernetes

import (
	manifests "github.com/antgroup/ai-vision/aivision-cli/deploy/kubernetes"
	"strings"
	"testing"
)

func TestNativeDistributionKubernetesDefaultsAndAssets(t *testing.T) {
	v := defaults()
	if v["NATIVE_PYTHON_RUNTIME"] != "true" {
		t.Error("native Kubernetes default not selected")
	}
	v["NATIVE_PYTHON_RUNTIME"] = "false"
	if err := (Config{Namespace: "aivision", Values: v}).validate(); err == nil || !strings.Contains(err.Error(), "native distribution") {
		t.Errorf("source Kubernetes config accepted: %v", err)
	}
	data, err := manifests.Files.ReadFile("database-init.yaml")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), "import os, time, pymysql") {
		t.Error("native Kubernetes asset embeds Python initializer")
	}
}
