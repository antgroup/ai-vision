//go:build native_distribution

package assets

import "embed"

// Explicit files prevent source-mode Python from entering the executable.
//
//go:embed components.json bootstrap/oceanbase.sh bootstrap/seekdb.sh
var Files embed.FS
