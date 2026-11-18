package runtime

import (
	"fmt"
	"github.com/antgroup/ai-vision/aivision-cli/internal/distribution"
)

// NativePythonRuntime selects images whose application and deployment helpers
// are native executables. Source/legacy images keep their existing fallback.
func (a *App) NativePythonRuntime() bool { return a.Vars["NATIVE_PYTHON_RUNTIME"] == "true" }

func configurePythonRuntime(bp *Blueprint, vars map[string]string) error {
	mode := vars["NATIVE_PYTHON_RUNTIME"]
	if distribution.NativeOnly {
		mode = "true"
		vars["NATIVE_PYTHON_RUNTIME"] = mode
	}
	if mode != "" && mode != "false" && mode != "true" {
		return fmt.Errorf("NATIVE_PYTHON_RUNTIME must be true or false")
	}
	if mode == "true" && bp.Metadata.Product != "aivision" {
		return fmt.Errorf("NATIVE_PYTHON_RUNTIME=true requires the product image blueprint")
	}
	vars["BE_MANAGE_CMD"] = "docker exec -w /app " + shq(vars["BE_CONTAINER"])
	if mode == "true" {
		vars["BE_MANAGE_CMD"] += " /app/manage"
	} else {
		vars["BE_MANAGE_CMD"] += " /bin/sh -ec " + shq(`if [ -x ./manage ]; then exec ./manage "$@"; fi; exec python manage.py "$@"`) + " aiv-manage"
	}
	return nil
}
