// Package proc terminates a supervised process tree (SIGTERM then SIGKILL).
package proc

import (
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/state"
)

// childPids returns the direct child pids of a process using pgrep -P.
func childPids(pid int) []int {
	out, err := exec.Command("pgrep", "-P", strconv.Itoa(pid)).Output()
	if err != nil {
		return nil
	}
	var pids []int
	for _, line := range strings.Fields(string(out)) {
		if p, convErr := strconv.Atoi(line); convErr == nil && p > 0 {
			pids = append(pids, p)
		}
	}
	return pids
}

// collectTree returns pid plus all descendants (depth-first), leaves first.
func collectTree(pid int, seen map[int]bool) []int {
	if seen[pid] {
		return nil
	}
	seen[pid] = true
	var order []int
	for _, ch := range childPids(pid) {
		order = append(order, collectTree(ch, seen)...)
	}
	return append(order, pid)
}

// TerminateTree gracefully stops a process and its descendants: SIGTERM first,
// then SIGKILL for stragglers after a short grace period.
func TerminateTree(pid int) error {
	if pid <= 0 || !state.PidAlive(pid) {
		return nil
	}
	tree := collectTree(pid, map[int]bool{})
	// SIGTERM in reverse (parents first is fine; children exit with parent).
	for _, p := range tree {
		_ = syscall.Kill(p, syscall.SIGTERM)
	}
	deadline := time.Now().Add(8 * time.Second)
	for time.Now().Before(deadline) {
		alive := false
		for _, p := range tree {
			if state.PidAlive(p) {
				alive = true
				break
			}
		}
		if !alive {
			return nil
		}
		time.Sleep(200 * time.Millisecond)
	}
	for _, p := range tree {
		if state.PidAlive(p) {
			_ = syscall.Kill(p, syscall.SIGKILL)
		}
	}
	time.Sleep(200 * time.Millisecond)
	return nil
}
