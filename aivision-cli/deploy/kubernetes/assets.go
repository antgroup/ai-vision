//go:build !native_distribution

// Package manifests holds the reviewed Kubernetes resources shipped in aiv.
// These resources are independent of the Docker blueprint and generator.
package manifests

import "embed"

//go:embed *.yaml dependencies/*.yaml
var Files embed.FS
