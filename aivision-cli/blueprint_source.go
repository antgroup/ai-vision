//go:build !native_distribution

package main

import _ "embed"

// defaultBlueprintYAML is the shipped AI Vision local stack recipe. It backs
// `aiv gen` and is the zero-config fallback when no -f file and no local
// blueprint.yaml is found.
//
//go:embed blueprints/aivision.yaml
var defaultBlueprintYAML string
