package sandboxbootstrap

import (
	"encoding/json"
	"path"
	"strings"
	"testing"
)

func TestWorkerPolicyAdmitsRuntimeAndWorkerWithoutHostAccess(t *testing.T) {
	var policy struct {
		Version int `json:"version"`
		Process struct {
			User  string `json:"run_as_user"`
			Group string `json:"run_as_group"`
		} `json:"process"`
		Landlock struct {
			Compatibility string `json:"compatibility"`
		} `json:"landlock"`
		Filesystem struct {
			IncludeWorkdir bool     `json:"include_workdir"`
			ReadOnly       []string `json:"read_only"`
			ReadWrite      []string `json:"read_write"`
		} `json:"filesystem_policy"`
		Network map[string]any `json:"network_policies"`
	}
	if err := json.Unmarshal([]byte(workerPolicy), &policy); err != nil {
		t.Fatal(err)
	}
	permitted := func(file string, write bool) bool {
		roots := append([]string{}, policy.Filesystem.ReadWrite...)
		if !write {
			roots = append(roots, policy.Filesystem.ReadOnly...)
		}
		for _, root := range roots {
			if file == root || strings.HasPrefix(path.Clean(file), root+"/") {
				return true
			}
		}
		return false
	}
	for _, file := range []string{"/bin/sleep", "/usr/bin/sleep", "/lib/ld-linux-aarch64.so.1", "/usr/local/bin/python3.11", "/etc/ld.so.cache", "/proc/self/status", "/dev/urandom", "/var/log/openshell", "/app/src/agentlens-rule-worker"} {
		if !permitted(file, false) {
			t.Errorf("fixed workload runtime cannot read/execute %s", file)
		}
	}
	for _, file := range []string{"/work/input.json", "/tmp/worker-cache", "/dev/null"} {
		if !permitted(file, true) {
			t.Errorf("fixed worker cannot write %s", file)
		}
	}
	for _, file := range []string{"/run/docker.sock", "/root/.ssh/id_rsa", "/var/lib/host-data", "/home/operator/.aivision/config.json"} {
		if permitted(file, false) || permitted(file, true) {
			t.Errorf("policy admitted unrelated host path %s", file)
		}
	}
	if permitted("/app/src/agentlens-rule-worker", true) || permitted("/usr/bin/sleep", true) {
		t.Fatal("runtime/worker code became writable")
	}
	if policy.Version != 1 || policy.Process.User != "1000" || policy.Process.Group != "1000" || policy.Landlock.Compatibility != "hard_requirement" || !policy.Filesystem.IncludeWorkdir || policy.Network == nil || len(policy.Network) != 0 {
		t.Fatal("fixed non-root, hard Landlock, deny-network boundary changed")
	}
}
