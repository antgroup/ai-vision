//go:build native_distribution

package main

import (
	_ "embed"
	"strings"
)

// The default recipe selects native helpers; explicit source overrides still fail.
//
//go:embed blueprints/aivision.yaml
var sourceBlueprintYAML string
var defaultBlueprintYAML = strings.Replace(sourceBlueprintYAML, `NATIVE_PYTHON_RUNTIME: "false"`, `NATIVE_PYTHON_RUNTIME: "true"`, 1)
