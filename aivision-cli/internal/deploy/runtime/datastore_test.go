package runtime

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/assets"
)

func datastoreApp(t *testing.T, file string, overrides map[string]string) (*App, error) {
	t.Helper()
	bp, err := blueprint.Load(filepath.Join("../../..", "blueprints", file))
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	bp.Config.Workdir = filepath.Join(dir, "work")
	bp.Config.SourceRoot = filepath.Join(dir, "source")
	for k, v := range overrides {
		bp.Config.Vars[k] = v
	}
	return NewApp(bp, NewLogger(false), false, false)
}

// The first consumers must agree on the selected account and endpoint; leaving
// one root@tenant template behind breaks only that service at deployment time.
func TestSeekdbUsesOneAccountAcrossServices(t *testing.T) {
	for _, file := range []string{"aivision.yaml", "aivision-source.yaml"} {
		t.Run(file, func(t *testing.T) {
			a, err := datastoreApp(t, file, map[string]string{"OB_BACKEND": "seekdb"})
			if err != nil {
				t.Fatal(err)
			}
			if a.Vars["OB_USER"] != "root" || !strings.HasPrefix(a.Vars["OB_IMAGE"], "oceanbase/seekdb@sha256:") {
				t.Fatalf("seekdb defaults unresolved: user=%q image=%q", a.Vars["OB_USER"], a.Vars["OB_IMAGE"])
			}
			be := *a.BP.Component("be")
			if got := a.ResolveEnv(be, a.ComponentVars(be))["DB_USER"]; got != "root" {
				t.Fatalf("backend user=%q", got)
			}
			al := *a.BP.Component("agentlens")
			env := a.ResolveEnv(al, a.ComponentVars(al))
			if !strings.HasPrefix(env["AGENTLENS_OB_URL"], "mysql+pymysql://root:") {
				t.Fatalf("AgentLens URL=%q", env["AGENTLENS_OB_URL"])
			}
			if !strings.HasPrefix(a.Vars["OCEANBASE_DSN"], "root:") {
				t.Fatalf("SigNoz DSN=%q", a.Vars["OCEANBASE_DSN"])
			}
			if file == "aivision-source.yaml" && !strings.Contains(env["AGENTLENS_OB_URL"], "@127.0.0.1:2881/") {
				t.Fatalf("host AgentLens lost its local endpoint: %q", env["AGENTLENS_OB_URL"])
			}
			c := a.BP.Component("oceanbase")
			if len(c.Volumes) != 1 || expand(c.Volumes[0], a.Vars) != "aivforge-seekdb-data-aivision:/var/lib/seekdb" {
				t.Fatalf("seekdb data not persisted: %#v", c.Volumes)
			}
		})
	}
}

func TestExternalSeekdbUsesExplicitEncodedAccount(t *testing.T) {
	a, err := datastoreApp(t, "aivision.yaml", map[string]string{"OB_BACKEND": "seekdb", "OB_MANAGED": "false", "OB_USER": "reader@team", "OB_HOST": "db.example", "OB_IMAGE": "oceanbase/seekdb:custom"})
	if err != nil {
		t.Fatal(err)
	}
	if a.BP.Component("oceanbase").IsEnabled() {
		t.Fatal("external seekdb started a bundled datastore")
	}
	if a.Vars["OB_IMAGE"] != "oceanbase/seekdb:custom" {
		t.Fatal("explicit image lost")
	}
	al := *a.BP.Component("agentlens")
	if got := a.ResolveEnv(al, a.ComponentVars(al))["AGENTLENS_OB_URL"]; !strings.HasPrefix(got, "mysql+pymysql://reader%40team:") {
		t.Fatalf("username was not URL-encoded: %q", got)
	}
	if !strings.Contains(a.Vars["OB_PREFLIGHT_CMD"], "OB_USER='reader@team'") {
		t.Fatal("external preflight does not use the selected account")
	}
	if strings.Contains(a.Vars["OB_PREFLIGHT_CMD"], "ALTER USER") {
		t.Fatal("external preflight must not change credentials")
	}
}

func TestDatastoreRejectsUnknownBackendAndUnsafeSeekdbCredentials(t *testing.T) {
	for _, vars := range []map[string]string{{"OB_BACKEND": "postgres"}, {"OB_BACKEND": "seekdb", "OB_TENANT_PASSWORD": ""}, {"OB_BACKEND": "seekdb", "OB_TENANT_PASSWORD": "bad'password"}, {"OB_BACKEND": "seekdb", "OB_USER": "not-root"}} {
		if _, err := datastoreApp(t, "aivision.yaml", vars); err == nil {
			t.Fatalf("accepted invalid datastore config: %#v", vars)
		}
	}
}

func TestDatastoreCannotChangeBackendInDeployedNamespace(t *testing.T) {
	for _, previous := range []string{"", "oceanbase", "seekdb"} {
		a, err := datastoreApp(t, "aivision.yaml", nil)
		if err != nil {
			t.Fatal(err)
		}
		if err := a.State.EnsureDirs(); err != nil {
			t.Fatal(err)
		}
		if err := a.State.SaveMeta(DeployMeta{DeployedAt: "2026-10-15", DatastoreBackend: previous}); err != nil {
			t.Fatal(err)
		}
		a.BP.Config.Vars["OB_BACKEND"] = "seekdb"
		_, err = NewApp(a.BP, NewLogger(false), false, false)
		if previous == "seekdb" && err != nil {
			t.Fatalf("same-backend rebuild rejected: %v", err)
		}
		if previous != "seekdb" && err == nil {
			t.Fatalf("changed deployed backend from %q", previous)
		}
	}
}

func TestFailedFirstDeployKeepsDatastoreBackendSelection(t *testing.T) {
	a, err := datastoreApp(t, "aivision.yaml", map[string]string{"OB_BACKEND": "seekdb"})
	if err != nil {
		t.Fatal(err)
	}
	// Stop at the first Docker mutation, before any successful-deploy metadata
	// exists. A later cast must still retain this namespace's datastore choice.
	docker := filepath.Join(t.TempDir(), "docker")
	fixture := `#!/bin/sh
case "$1 $2" in
  'container inspect') echo "Error: No such container: $3" >&2; exit 1;;
  'volume inspect') echo "Error response from daemon: get $3: no such volume" >&2; exit 1;;
esac
exit 43
`
	if err := os.WriteFile(docker, []byte(fixture), 0o700); err != nil {
		t.Fatal(err)
	}
	a.Docker.Bin = docker
	if err := a.Deploy(); err == nil {
		t.Fatal("expected Docker network failure")
	}
	if _, err := a.State.LoadMeta(); !os.IsNotExist(err) {
		t.Fatalf("unexpected success metadata: %v", err)
	}
	a.BP.Config.Vars["OB_BACKEND"] = "oceanbase"
	if _, err := NewApp(a.BP, NewLogger(false), false, false); err == nil {
		t.Fatal("partial deployment allowed a datastore backend change")
	}
}

// Docker and SQL are external boundaries. Execute the actual rendered startup
// shell against a stateful SQL fixture, checking first-use authentication and
// subsequent nonempty-volume behavior without simulating the full platform.
func TestSeekdbOfflineInitializationPreservesCredentials(t *testing.T) {
	for _, command := range []string{"timeout", "flock"} {
		if _, err := exec.LookPath(command); err != nil {
			t.Skipf("%s is required for the external Docker/SQL fixtures", command)
		}
	}
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 is required for the external Docker/SQL fixtures")
	}
	dir := t.TempDir()
	volume := filepath.Join(dir, "volume")
	if err := os.Mkdir(volume, 0o700); err != nil {
		t.Fatal(err)
	}
	writeExecutable := func(name, body string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	writeExecutable("seekdb", "#!/bin/sh\ntrap 'exit 0' USR1\ntouch \"$AIV_TEST_DIR/volume/initialized\"\nwhile :; do sleep 0.1; done\n")
	writeExecutable("mysql", "#!"+python+"\n"+seekdbSQLFixture)
	writeExecutable("docker", "#!"+python+"\n"+seekdbDockerFixture)
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("AIV_TEST_DIR", dir)
	a, err := datastoreApp(t, "aivision.yaml", map[string]string{"OB_BACKEND": "seekdb", "OB_TENANT_PASSWORD": "first-private-password"})
	if err != nil {
		t.Fatal(err)
	}
	if err := a.prepareDatastoreAssets(); err != nil {
		t.Fatal(err)
	}
	c := *a.BP.Component("oceanbase")
	var raw Step
	for _, st := range c.PreStart {
		if st.Name == "initialize or verify seekdb credentials offline" {
			raw = st
		}
	}
	run := func(password string) (string, error) {
		t.Helper()
		vars := a.ComponentVars(c)
		vars["OB_TENANT_PASSWORD"] = password
		st := expandStep(raw, vars)
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, "bash", "-c", st.Shell)
		cmd.WaitDelay = time.Second
		cmd.Env = os.Environ()
		for k, v := range st.Env {
			cmd.Env = append(cmd.Env, k+"="+v)
		}
		out, err := cmd.CombinedOutput()
		if ctx.Err() != nil {
			t.Fatalf("offline initialization did not terminate promptly: %v\n%s", ctx.Err(), out)
		}
		if strings.Contains(string(out), password) {
			t.Fatal("password leaked in startup output")
		}
		return string(out), err
	}
	if out, err := run("first-private-password"); err != nil {
		t.Fatalf("fresh initialization failed: %v\n%s", err, out)
	}
	if out, err := run("first-private-password"); err != nil {
		t.Fatalf("existing-volume verification failed: %v\n%s", err, out)
	}
	if out, err := run("wrong-private-password"); err == nil || !strings.Contains(out, "volume rejected password") {
		t.Fatalf("existing-volume credentials were not rejected explicitly: %v\n%s", err, out)
	}
	changes, err := os.ReadFile(filepath.Join(dir, "alter-count"))
	if err != nil || string(changes) != "1" {
		t.Fatalf("expected exactly one fresh-volume password initialization: %q %v", changes, err)
	}
	password, _ := os.ReadFile(filepath.Join(volume, "password"))
	if string(password) != "first-private-password" {
		t.Fatal("existing-volume password was modified")
	}
	// Model a crash after engine files were created, before ALTER USER. Only
	// our durable initialization marker authorizes retrying the empty account.
	if err := os.Remove(filepath.Join(volume, "password")); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(filepath.Join(volume, ".aiv-credentials-verified")); err != nil {
		t.Fatal(err)
	}
	if out, err := run("recovered-private-password"); err == nil {
		t.Fatalf("adopted unknown nonempty passwordless storage: %s", out)
	}
	if err := os.WriteFile(filepath.Join(volume, ".aiv-initializing"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	if out, err := run("recovered-private-password"); err != nil {
		t.Fatalf("owned partial initialization did not recover: %v\n%s", err, out)
	}
	password, _ = os.ReadFile(filepath.Join(volume, "password"))
	if string(password) != "recovered-private-password" {
		t.Fatal("partial initialization did not install the configured password")
	}
	if _, err := os.Stat(filepath.Join(volume, ".aiv-initializing")); !os.IsNotExist(err) {
		t.Fatal("successful retry did not clear the initialization marker")
	}
	// The fixture scales deadlines down, but executes the actual canonical
	// timeout/cleanup code against a SQL client that never replies.
	t.Setenv("AIV_TEST_SQL_HANG", "1")
	if out, err := run("recovered-private-password"); err == nil {
		t.Fatalf("hung SQL did not hit the bounded initialization deadline: %s", out)
	}
	if err := exec.Command("flock", "-n", filepath.Join(volume, ".aiv-volume.lock"), "true").Run(); err != nil {
		t.Fatal("timed-out initializer left a database process holding the data volume")
	}
}

func TestSeekdbLifecycleAssetIsPrivateAndServingUsesSharedScript(t *testing.T) {
	a, err := datastoreApp(t, "aivision.yaml", map[string]string{"OB_BACKEND": "seekdb"})
	if err != nil {
		t.Fatal(err)
	}
	path := a.Vars["SEEKDB_SCRIPT"]
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("constructing an app must not materialize execution files")
	}
	if err := a.prepareDatastoreAssets(); err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	embedded, _ := assets.Files.ReadFile("bootstrap/seekdb.sh")
	if !bytes.Equal(content, embedded) {
		t.Fatal("CLI database lifecycle diverged from the canonical embedded asset")
	}
	for name, mode := range map[string]os.FileMode{path: 0600, filepath.Dir(path): 0700} {
		info, err := os.Stat(name)
		if err != nil || info.Mode().Perm() != mode {
			t.Fatalf("private runtime asset mode: %s", name)
		}
	}
	c := *a.BP.Component("oceanbase")
	commandBefore := append([]string{}, c.Command...)
	mountsBefore := append([]string{}, c.Mounts...)
	dir := t.TempDir()
	record := filepath.Join(dir, "args")
	docker := filepath.Join(dir, "docker")
	if err := os.WriteFile(docker, []byte("#!/bin/sh\ncase \"$1\" in inspect) exit 1;; container) exit 0;; esac\nprintf '%s\\n' \"$@\" > "+shq(record)+"\n"), 0700); err != nil {
		t.Fatal(err)
	}
	a.Docker.Bin = docker
	if err := a.startContainer(c, a.ComponentVars(c)); err != nil {
		t.Fatal(err)
	}
	recorded, _ := os.ReadFile(record)
	args := string(recorded)
	if !strings.Contains(args, "type=bind,source="+path+",target=/opt/aivision/seekdb.sh,readonly") ||
		!strings.HasSuffix(args, "/opt/aivision/seekdb.sh\nserve\n") ||
		!strings.Contains(args, "aivforge-seekdb-data-aivision:/var/lib/seekdb") {
		t.Fatal("serving must use the embedded lifecycle without changing the physical volume")
	}
	if strings.Join(c.Command, "\n") != strings.Join(commandBefore, "\n") ||
		strings.Join(c.Mounts, "\n") != strings.Join(mountsBefore, "\n") {
		t.Fatal("runtime execution mutated the blueprint used by source/generic exports")
	}
}

func TestManagedSeekdbHealthUsesBoundedSharedAndLegacyProbes(t *testing.T) {
	t.Setenv("ROOT_PASSWORD", "unrelated-host-root-password")
	a, err := datastoreApp(t, "aivision.yaml", map[string]string{"OB_BACKEND": "seekdb"})
	if err != nil {
		t.Fatal(err)
	}
	c := *a.BP.Component("oceanbase")
	health := expandHealth(c.Health, a.ComponentVars(c)).Exec
	for _, want := range []string{
		"docker exec ", "if [ -r /opt/aivision/seekdb.sh ]",
		"exec /bin/sh /opt/aivision/seekdb.sh health",
		`MYSQL_PWD="$ROOT_PASSWORD" exec timeout --signal=TERM --kill-after=1s 3s`,
		"--connect-timeout=2",
	} {
		if !strings.Contains(health, want) {
			t.Fatalf("managed seekdb health lost the shared probe or bounded legacy fallback: %q", want)
		}
	}
	if strings.Contains(health, a.Vars["OB_TENANT_PASSWORD"]) {
		t.Fatal("health command exposes the actual database password")
	}
	if strings.Contains(health, "unrelated-host-root-password") {
		t.Fatal("container password reference was expanded on the host")
	}
}

const seekdbSQLFixture = `import os, pathlib, sys, time
root = pathlib.Path(os.environ['AIV_TEST_DIR'])
if os.environ.get('AIV_TEST_SQL_HANG') == '1':
    time.sleep(60)
password_file = root / 'volume/password'
expected = password_file.read_text() if password_file.exists() else ''
if os.environ.get('MYSQL_PWD', '') != expected:
    print('ERROR 1045 (28000): Access denied', file=sys.stderr)
    sys.exit(1)
args = sys.argv[1:]
query = args[args.index('-e')+1] if '-e' in args else sys.stdin.read()
if query.startswith('ALTER USER'):
    password_file.write_text(query.split("'")[3])
    count = root / 'alter-count'
    count.write_text(str(int(count.read_text()) + 1) if count.exists() else '1')
elif 'version()' in query:
    print('OceanBase seekdb fixture')
else:
    print('1')
`

const seekdbDockerFixture = `import json, os, pathlib, subprocess, sys
root = pathlib.Path(os.environ['AIV_TEST_DIR'])
args = sys.argv[1:]
if args[:2] == ['image', 'inspect'] or args[:2] == ['volume', 'create']:
    sys.exit(0)
if args[:2] == ['container', 'inspect']:
    sys.exit(1)
if args[:2] == ['container', 'ls']:
    sys.exit(0)
if args[:2] == ['network', 'inspect']:
    sys.exit(1)
if args[:2] == ['network', 'create']:
    assert '--internal' in args, 'initializer network can route externally'
    print('owned-internal-network')
    sys.exit(0)
if args[:2] == ['network', 'rm']:
    assert args[2] == 'owned-internal-network', 'removed an unrelated network'
    sys.exit(0)
if args[0] == 'create':
    assert args[args.index('--network')+1] == 'owned-internal-network', 'initializer joined an application network'
    assert '-p' not in args, 'initializer publishes a port'
    assert args[args.index('-v')+1].endswith(':/var/lib/seekdb'), 'wrong data volume'
    assert os.environ['AIV_SEEKDB_PASSWORD'] not in ' '.join(args), 'password on Docker command line'
    assert args[-2:] == ['/opt/aivision/seekdb.sh', 'initialize'], 'not using shared initializer'
    mount = args[args.index('--mount')+1]
    assert 'target=/opt/aivision/seekdb.sh,readonly' in mount, 'script is not mounted read-only'
    source = mount.split('source=', 1)[1].split(',target=', 1)[0]
    script = pathlib.Path(source).read_text().replace('/var/lib/seekdb', str(root / 'volume'))
    script = script.replace('/usr/bin/seekdb', str(root / 'seekdb')).replace('/usr/bin/mysql', str(root / 'mysql'))
    script = script.replace('/tmp/aiv-seekdb-', str(root / 'log-'))
    if os.environ.get('AIV_TEST_SQL_HANG') == '1':
        script = script.replace('1200s', '2s').replace('120s', '2s').replace('30s', '1s')
        script = script.replace('-lt 90', '-lt 1')
    (root / 'script').write_text(script)
elif args[0] == 'start':
    result = subprocess.run(['/bin/sh', str(root/'script'), 'initialize'])
    (root/'exit-code').write_text(str(result.returncode))
elif args[0] == 'inspect':
    print((root/'exit-code').read_text())
elif args[0] in ('rm', 'stop'):
    pass
else:
    raise RuntimeError('Unexpected Docker invocation: ' + repr(args))
`

func TestSeekdbMemoryBudgetPreservesLegacyOverride(t *testing.T) {
	for _, tc := range []struct {
		vars map[string]string
		want string
	}{
		{map[string]string{}, "2G"},
		{map[string]string{"SEEKDB_MEMORY_LIMIT": "3G"}, "3G"},
		{map[string]string{"SEEKDB_MEMORY_LIMIT": "3G", "SEEKDB_MEMORY_BUDGET": "1G"}, "1G"},
	} {
		tc.vars["OB_BACKEND"] = "seekdb"
		a, err := datastoreApp(t, "aivision.yaml", tc.vars)
		if err != nil {
			t.Fatal(err)
		}
		c := *a.BP.Component("oceanbase")
		if got := a.ResolveEnv(c, a.ComponentVars(c))["AIV_SEEKDB_MEMORY_BUDGET"]; got != tc.want {
			t.Fatalf("effective memory budget=%q want %q", got, tc.want)
		}
	}
}
