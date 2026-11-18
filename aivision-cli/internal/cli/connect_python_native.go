//go:build native_distribution

package cli

import "fmt"

func resolvePython(explicit string) (string, error) {
	return "", fmt.Errorf("connect: native distribution requires a native installer; source Python execution is unsupported")
}
