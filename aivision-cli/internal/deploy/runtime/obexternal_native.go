//go:build native_distribution

package runtime

// Native builds never embed or execute a source preflight program.
const obPreflightPython = ""
