//go:build native_distribution

package manifests

import "embed"

// Native templates are projected from canonical sources before linking.
//
//go:embed agentlens.yaml backend.yaml certificate-check.yaml config.yaml database.yaml frontend.yaml gateway.yaml ingress-controller.yaml ingress.yaml namespace.yaml signoz.yaml tenant.yaml native/*.yaml dependencies/*.yaml
var nativeFiles embed.FS

type nativeFileSystem struct{ embed.FS }

func (f nativeFileSystem) ReadFile(name string) ([]byte, error) {
	if name == "database-init.yaml" || name == "bootstrap.yaml" {
		name = "native/" + name
	}
	return f.FS.ReadFile(name)
}

var Files = nativeFileSystem{nativeFiles}
