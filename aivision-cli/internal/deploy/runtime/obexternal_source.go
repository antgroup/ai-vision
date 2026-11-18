//go:build !native_distribution

package runtime

import "github.com/antgroup/ai-vision/aivision-cli/internal/deploy/assets"

// obPreflightPython is shared with the Compose bootstrap job. The generated
// bundle is refreshed from deploy/bootstrap/database-init.py.
var obPreflightPython = func() string {
	data, err := assets.Files.ReadFile("bootstrap/database-init.py")
	if err != nil {
		panic(err)
	}
	return string(data)
}()
