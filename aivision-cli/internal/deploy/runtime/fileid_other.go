//go:build !unix

package runtime

import "os"

// fileIdentity is unavailable on this platform.
func fileIdentity(os.FileInfo) (dev, ino uint64, ok bool) {
	return 0, 0, false
}
