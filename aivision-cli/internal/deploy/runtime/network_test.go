package runtime

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// This executable substitutes only the external Docker CLI. The real runtime
// must create the selected network before either a probe or a service joins it.
const networkDockerFixture = `#!/bin/sh
printf '%s\n' "$*" >> "$AIV_NETWORK_TEST_DIR/calls"
case "$1 $2" in
  'container inspect') echo "Error: No such container: $3" >&2; exit 1;;
  'volume inspect') echo "Error response from daemon: get $3: no such volume" >&2; exit 1;;
  'version --format') echo fixture; exit 0;;
  'image inspect') exit 0;;
  'network inspect') test -f "$AIV_NETWORK_TEST_DIR/$3"; exit $?;;
  'network create') touch "$AIV_NETWORK_TEST_DIR/$3"; exit $?;;
  inspect*) exit 1;;
  run*)
    network=
    while [ "$#" -gt 0 ]; do
      if [ "$1" = --network ]; then shift; network=$1; fi
      shift
    done
    if [ "$network" != "$AIV_NETWORK_TEST_EXPECTED" ]; then
      echo "wrong network: $network" >&2; exit 42
    fi
    if [ -n "$network" ] && [ ! -f "$AIV_NETWORK_TEST_DIR/$network" ]; then
      echo "network not created before use: $network" >&2; exit 43
    fi
    echo fixture-container; exit 0;;
esac
echo "unexpected Docker invocation" >&2
exit 44
`

func TestCastUsesEffectiveNetworkForCreationProbeAndContainers(t *testing.T) {
	for _, tc := range []struct {
		name       string
		configured string
		override   string
		want       string
	}{
		{"default", "default-net", "", "default-net"},
		{"override", "default-net", "custom-net", "custom-net"},
		{"template-default", "${NAMESPACE}-net", "", "network-test-net"},
		{"template-override", "default-net", "${NAMESPACE}-custom", "network-test-custom"},
	} {
		for _, external := range []bool{false, true} {
			name := tc.name + "/bundled"
			if external {
				name = tc.name + "/external-ob"
			}
			t.Run(name, func(t *testing.T) {
				dir := t.TempDir()
				docker := filepath.Join(dir, "docker")
				if err := os.WriteFile(docker, []byte(networkDockerFixture), 0o755); err != nil {
					t.Fatal(err)
				}
				t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
				t.Setenv("DOCKER_BIN", docker)
				t.Setenv("AIV_NETWORK_TEST_DIR", dir)
				t.Setenv("AIV_NETWORK_TEST_EXPECTED", tc.want)
				vars := map[string]string{"OB_MANAGED": "true"}
				if tc.override != "" {
					vars["DOCKER_NETWORK"] = tc.override
				}
				if external {
					vars["OB_MANAGED"] = "false"
					vars["OB_HOST"] = "external-ob.example.test"
					vars["OB_PREFLIGHT_IMAGE"] = "fixture-probe"
				}
				bp := &Blueprint{
					Metadata: Metadata{Namespace: "network-test"},
					Config:   Config{DockerNetwork: tc.configured, Workdir: filepath.Join(dir, "work"), SourceRoot: filepath.Join(dir, "source"), Vars: vars},
					Components: []Component{
						{Name: "explicit", Kind: "container", Image: "fixture", Network: "${DOCKER_NETWORK}"},
						{Name: "default", Kind: "container", Image: "fixture"},
					},
				}
				app, err := NewApp(bp, NewLogger(false), false, false)
				if err != nil {
					t.Fatal(err)
				}
				if err := app.Cast(true, false); err != nil {
					t.Fatalf("cast failed with an initially absent network: %v", err)
				}
				calls, err := os.ReadFile(filepath.Join(dir, "calls"))
				if err != nil {
					t.Fatal(err)
				}
				if strings.Count(string(calls), "network create ") != 1 || !strings.Contains(string(calls), "network create "+tc.want+"\n") {
					t.Fatalf("must create only the effective network once:\n%s", calls)
				}
				if external && !strings.Contains(string(calls), "run --rm -i ") {
					t.Fatalf("external OceanBase was not probed:\n%s", calls)
				}
			})
		}
	}
}
