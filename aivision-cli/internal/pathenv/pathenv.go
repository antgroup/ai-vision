// Package pathenv augments PATH so the CLI works when launched from a minimal
// context (launchd, cron, a bare `sh -c`) where the interactive-shell PATH has
// not been set up.
package pathenv

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Augment makes aivision robust when launched from a minimal context
// (launchd, cron, a bare `sh -c`, another process manager) where the user's
// interactive-shell PATH — and therefore nvm/pyenv/homebrew/corepack tool
// locations — has not been set up. It appends well-known tool directories that
// exist on disk but are missing from PATH, so `gauge`, `forge` and `cast` can
// still locate node, pnpm, uv, go, docker, mysql, etc.
//
// Existing PATH entries are never reordered or shadowed; we only append.
func Augment() {
	home, err := os.UserHomeDir()
	if err != nil {
		home = ""
	}
	cur := os.Getenv("PATH")
	have := map[string]bool{}
	for _, p := range filepath.SplitList(cur) {
		if p != "" {
			have[p] = true
		}
	}

	candidates := []string{
		"/opt/homebrew/bin",
		"/opt/homebrew/sbin",
		"/usr/local/bin",
		"/usr/local/opt/mysql-client/bin",
	}
	if home != "" {
		candidates = append(candidates,
			filepath.Join(home, ".local", "bin"),
			filepath.Join(home, ".cargo", "bin"),
			filepath.Join(home, ".pyenv", "shims"),
			filepath.Join(home, ".pyenv", "bin"),
			filepath.Join(home, "Library", "pnpm"),
			filepath.Join(home, ".local", "share", "pnpm"),
			filepath.Join(home, ".hermes", "bin"),
			filepath.Join(home, "go", "bin"),
		)
		// nvm: add every installed node version's bin (latest last so it wins
		// only if nothing else provided node earlier in PATH).
		if vers, err := filepath.Glob(filepath.Join(home, ".nvm", "versions", "node", "*", "bin")); err == nil {
			sort.Strings(vers)
			candidates = append(candidates, vers...)
		}
		// fnm / volta common locations
		candidates = append(candidates,
			filepath.Join(home, ".volta", "bin"),
			filepath.Join(home, ".fnm", "current", "bin"),
		)
	}

	var added []string
	for _, dir := range candidates {
		if dir == "" || have[dir] {
			continue
		}
		if fi, statErr := os.Stat(dir); statErr == nil && fi.IsDir() {
			added = append(added, dir)
			have[dir] = true
		}
	}
	if len(added) == 0 {
		return
	}
	newPath := cur
	if newPath != "" && !strings.HasSuffix(newPath, string(os.PathListSeparator)) {
		newPath += string(os.PathListSeparator)
	}
	newPath += strings.Join(added, string(os.PathListSeparator))
	_ = os.Setenv("PATH", newPath)
	// Record what we injected so `gauge -v` can explain a resolved tool path.
	_ = os.Setenv("AIVISION_PATH_AUGMENTED", strings.Join(added, string(os.PathListSeparator)))
}
