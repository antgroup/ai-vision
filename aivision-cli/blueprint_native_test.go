//go:build native_distribution

package main

import (
	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"testing"
)

func TestNativeDistributionDefaultBlueprint(t *testing.T) {
	bp, err := blueprint.Parse([]byte(defaultBlueprintYAML))
	if err != nil {
		t.Fatal(err)
	}
	if bp.Config.Vars["NATIVE_PYTHON_RUNTIME"] != "true" {
		t.Fatal("native CLI default selected source runtime")
	}
}
