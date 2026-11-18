package runtime

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/assets"
)

const seekdbImage = "oceanbase/seekdb@sha256:500910bda197c0bc5f8bbada10c6ad727807308826818c0ea0391844dedc5cc2"

// Only blueprints declaring OB_BACKEND opt into datastore adaptation. Older
// custom blueprints retain their own commands, credentials and volume layout.
func datastoreDefaults(vars map[string]string) error {
	backend, declared := vars["OB_BACKEND"]
	if !declared {
		return nil
	}
	if backend == "" {
		backend = "oceanbase"
	}
	if backend != "oceanbase" && backend != "seekdb" {
		return fmt.Errorf("OB_BACKEND must be oceanbase or seekdb")
	}
	vars["OB_BACKEND"] = backend
	if vars["OB_IMAGE"] == "" {
		vars["OB_IMAGE"] = "oceanbase/oceanbase-ce:latest"
		if backend == "seekdb" {
			vars["OB_IMAGE"] = seekdbImage
		}
	}
	if vars["OB_USER"] == "" {
		vars["OB_USER"] = "root@${OB_TENANT}"
		if backend == "seekdb" {
			vars["OB_USER"] = "root"
		}
	}
	return nil
}

func configureDatastore(bp *Blueprint, vars map[string]string, state *State) error {
	if err := checkDatastoreBackend(state, vars["OB_BACKEND"]); err != nil {
		return err
	}
	if vars["OB_BACKEND"] != "seekdb" {
		if vars["OB_BACKEND"] == "oceanbase" && bp.Component("oceanbase") != nil {
			vars["OCEANBASE_SCRIPT"] = filepath.Join(state.StateDir, "bootstrap", "oceanbase.sh")
		}
		return nil
	}
	if !regexp.MustCompile(`^[A-Za-z0-9_-]+$`).MatchString(vars["OB_TENANT_PASSWORD"]) {
		return fmt.Errorf("seekdb requires a nonempty URL-safe OB_TENANT_PASSWORD (letters, digits, underscore or hyphen)")
	}
	c := bp.Component("oceanbase")
	if c == nil {
		return fmt.Errorf("OB_BACKEND=seekdb requires the oceanbase datastore component")
	}
	managedValue := strings.ToLower(strings.TrimSpace(vars["OB_MANAGED"]))
	managed := managedValue != "false" && managedValue != "0" && managedValue != "no" && managedValue != "off"
	if managed && vars["OB_USER"] != "root" {
		return fmt.Errorf("managed seekdb requires OB_USER=root; use OB_MANAGED=false for an existing account")
	}
	for key, value := range map[string]string{"SEEKDB_MEMORY_LIMIT": "2G", "SEEKDB_CPU_COUNT": "2", "SEEKDB_DATAFILE_SIZE": "2G", "SEEKDB_LOG_DISK_SIZE": "2G"} {
		if vars[key] == "" {
			vars[key] = value
		}
	}
	if vars["SEEKDB_MEMORY_BUDGET"] == "" {
		vars["SEEKDB_MEMORY_BUDGET"] = vars["SEEKDB_MEMORY_LIMIT"]
	}
	if vars["SEEKDB_CONTAINER_MEMORY"] == "" {
		vars["SEEKDB_CONTAINER_MEMORY"] = "4G"
	}
	c.MemoryLimit = "${SEEKDB_CONTAINER_MEMORY}"
	vars["SEEKDB_VOLUME"] = "aivforge-seekdb-data-" + vars["NAMESPACE"]
	// The fixed image's upstream wrapper never completes ROOT_PASSWORD setup.
	// Both CLI and Compose initialize and serve through the shared asset.
	c.Title = "AI Vision Database (seekdb)"
	c.Volumes = []string{"${SEEKDB_VOLUME}:/var/lib/seekdb"}
	c.Entrypoint = []string{"/usr/bin/seekdb"}
	c.Command = []string{"--nodaemon", "--base-dir=/var/lib/seekdb", "--port=2881",
		"--parameter", "memory_budget=${SEEKDB_MEMORY_BUDGET}", "--parameter", "cpu_count=${SEEKDB_CPU_COUNT}",
		"--parameter", "datafile_size=${SEEKDB_DATAFILE_SIZE}", "--parameter", "log_disk_size=${SEEKDB_LOG_DISK_SIZE}"}
	c.Env = map[string]string{
		"ROOT_PASSWORD":       "${OB_TENANT_PASSWORD}", // Existing MYSQL_CMD consumers.
		"AIV_SEEKDB_PASSWORD": "${OB_TENANT_PASSWORD}", "AIV_SEEKDB_MEMORY_BUDGET": "${SEEKDB_MEMORY_BUDGET}",
		"AIV_SEEKDB_CPU": "${SEEKDB_CPU_COUNT}", "AIV_SEEKDB_DATA": "${SEEKDB_DATAFILE_SIZE}",
		"AIV_SEEKDB_LOG": "${SEEKDB_LOG_DISK_SIZE}",
	}
	vars["MYSQL_CMD"] = "docker exec " + shq(vars["OB_CONTAINER"]) + " /bin/sh -ec " + shq(`MYSQL_PWD="$ROOT_PASSWORD" exec /usr/bin/mysql --no-defaults --protocol=TCP -h127.0.0.1 -P2881 -uroot "$@"`) + " sh"
	if managed {
		vars["SEEKDB_SCRIPT"] = filepath.Join(state.StateDir, "bootstrap", "seekdb.sh")
		if c.Health != nil {
			health := *c.Health
			// New deployments use the same bounded probe as Compose. A newer
			// CLI can still inspect a legacy container before its first recast:
			// those have ROOT_PASSWORD but no embedded lifecycle mount.
			vars["SEEKDB_HEALTH_CMD"] = "docker exec " + shq(vars["OB_CONTAINER"]) + " /bin/sh -ec " + shq(`if [ -r /opt/aivision/seekdb.sh ]; then exec /bin/sh /opt/aivision/seekdb.sh health; fi
MYSQL_PWD="$ROOT_PASSWORD" exec timeout --signal=TERM --kill-after=1s 3s /usr/bin/mysql --no-defaults --protocol=TCP --connect-timeout=2 -h127.0.0.1 -P2881 -uroot -e 'SELECT 1'`)
			// Resolve the command once, preserving the container's literal
			// $ROOT_PASSWORD reference through the blueprint expansion layer.
			health.Exec = "${SEEKDB_HEALTH_CMD}"
			c.Health = &health
		}
		vars["SEEKDB_INIT_CMD"] = seekdbPrepareShell
		c.PreStart = append(c.PreStart, Step{Name: "initialize or verify seekdb credentials offline", Shell: "${SEEKDB_INIT_CMD}", Env: map[string]string{
			"AIV_SEEKDB_IMAGE": "${OB_IMAGE}", "AIV_SEEKDB_CONTAINER": "${OB_CONTAINER}", "AIV_SEEKDB_VOLUME": "${SEEKDB_VOLUME}",
			"AIV_SEEKDB_PASSWORD": "${OB_TENANT_PASSWORD}", "AIV_SEEKDB_MEMORY_BUDGET": "${SEEKDB_MEMORY_BUDGET}", "AIV_SEEKDB_CPU": "${SEEKDB_CPU_COUNT}",
			"AIV_SEEKDB_DATA": "${SEEKDB_DATAFILE_SIZE}", "AIV_SEEKDB_LOG": "${SEEKDB_LOG_DISK_SIZE}",
			"AIV_SEEKDB_CONTAINER_MEMORY": "${SEEKDB_CONTAINER_MEMORY}",
			"AIV_SEEKDB_SCRIPT":           "${SEEKDB_SCRIPT}",
			"AIV_SEEKDB_STOP_RECORD":      filepath.Join(state.StateDir, "lifecycle-stops", "database-id"),
		}})
	}
	return nil
}

// Only execution materializes the embedded asset. Status and generation do not
// need a source checkout and do not create runtime files.
func (a *App) prepareDatastoreAssets() error {
	if !a.OBManaged() {
		return nil
	}
	asset, path := "", ""
	switch a.Vars["OB_BACKEND"] {
	case "seekdb":
		asset, path = "seekdb.sh", a.Vars["SEEKDB_SCRIPT"]
	case "oceanbase":
		asset, path = "oceanbase.sh", a.Vars["OCEANBASE_SCRIPT"]
	default:
		return nil
	}
	if path == "" {
		return nil
	}
	data, err := assets.Files.ReadFile("bootstrap/" + asset)
	if err != nil {
		return fmt.Errorf("read embedded database lifecycle: %w", err)
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	info, err := os.Lstat(dir)
	if err != nil || !info.IsDir() {
		return fmt.Errorf("database lifecycle directory must be a real directory: %s", dir)
	}
	if err := os.Chmod(dir, 0700); err != nil {
		return err
	}
	file, err := os.CreateTemp(dir, "."+asset+"-")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if _, err := file.Write(data); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if err := os.Rename(file.Name(), path); err != nil {
		return err
	}
	return nil
}

func checkDatastoreBackend(state *State, backend string) error {
	previous := ""
	data, err := os.ReadFile(filepath.Join(state.StateDir, "datastore-backend"))
	if err == nil {
		previous = strings.TrimSpace(string(data))
		if previous != "oceanbase" && previous != "seekdb" {
			return fmt.Errorf("namespace %s has an invalid datastore backend marker; refusing to guess its data layout", state.Namespace)
		}
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("read datastore backend: %w", err)
	} else {
		prior, err := state.LoadMeta()
		if err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("read prior deployment: %w", err)
		}
		if prior != nil && prior.DeployedAt != "" {
			previous = prior.DatastoreBackend
			if previous == "" {
				previous = "oceanbase"
			}
		}
	}
	if previous != "" && previous != backend {
		return fmt.Errorf("namespace %s already uses %s; use a new namespace for %s (changing OB_BACKEND does not migrate data)", state.Namespace, previous, backend)
	}
	return nil
}

// Reserve the namespace's data layout before the first Docker mutation. Keep
// the marker after partial failure and non-purge destroy. Exclusive creation
// prevents concurrent casts from selecting different backends.
func (a *App) reserveDatastoreBackend() error {
	backend, declared := a.Vars["OB_BACKEND"]
	if !declared {
		return nil
	}
	if err := checkDatastoreBackend(a.State, backend); err != nil {
		return err
	}
	f, err := os.OpenFile(filepath.Join(a.State.StateDir, "datastore-backend"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if os.IsExist(err) {
		return checkDatastoreBackend(a.State, backend)
	}
	if err != nil {
		return fmt.Errorf("reserve datastore backend: %w", err)
	}
	if _, err := f.WriteString(backend + "\n"); err != nil {
		f.Close()
		return fmt.Errorf("write datastore backend: %w", err)
	}
	if err := f.Close(); err != nil {
		return fmt.Errorf("close datastore backend: %w", err)
	}
	return nil
}

// The wrapper retains CLI physical names and its isolated initialization
// network. Engine, credentials, retry and volume-lock logic live in the shared
// embedded script, which is privately materialized under this deployment's state.
const seekdbPrepareShell = `set -eu
test -f "$AIV_SEEKDB_SCRIPT" || { echo "embedded seekdb lifecycle asset is missing" >&2; exit 1; }
if ! docker image inspect "$AIV_SEEKDB_IMAGE" >/dev/null 2>&1; then docker pull "$AIV_SEEKDB_IMAGE"; fi
docker volume create "$AIV_SEEKDB_VOLUME" >/dev/null
stop_record=$AIV_SEEKDB_STOP_RECORD
record_stop() (
  umask 077
  directory=$(dirname "$stop_record")
  mkdir -p "$directory"
  temporary=$(mktemp "$directory/.database-stop-XXXXXX")
  trap 'rm -f "$temporary"' EXIT
  printf '%s' "$container_id" > "$temporary"
  mv "$temporary" "$stop_record"
)
if docker container inspect "$AIV_SEEKDB_CONTAINER" >/dev/null 2>&1; then
  container_id=$(docker inspect --format '{{.Id}}' "$AIV_SEEKDB_CONTAINER")
  case "$container_id" in ''|*[!a-zA-Z0-9_-]*) echo "cannot verify database container identity" >&2; exit 1;; esac
  pending=false
  if [ -e "$stop_record" ] || [ -L "$stop_record" ]; then
    previous=$(cat "$stop_record")
    [ "$(wc -c < "$stop_record")" -eq "${#previous}" ] || { echo "invalid pending database stop record" >&2; exit 1; }
    case "$previous" in ''|*[!a-zA-Z0-9_-]*) echo "invalid pending database stop record" >&2; exit 1;; esac
    if [ "$previous" = "$container_id" ]; then pending=true; else rm "$stop_record"; fi
  fi
  running=$(docker inspect --format '{{.State.Running}}' "$container_id")
  case "$running" in true|false) :;; *) echo "cannot verify database running state" >&2; exit 1;; esac
  if [ "$running" = true ]; then
    record_stop
    pending=true
    old_entrypoint=$(docker inspect --format '{{json .Config.Entrypoint}}' "$AIV_SEEKDB_CONTAINER")
    if [ "$old_entrypoint" = '["/usr/bin/seekdb"]' ]; then
      # Legacy native PID 1 needs USR1, the engine's supported graceful signal.
      # docker kill --signal also supports older clients without stop --signal.
      docker kill --signal SIGUSR1 "$AIV_SEEKDB_CONTAINER" >/dev/null
      stop_attempt=0
      while :; do
        running=$(docker inspect --format '{{.State.Running}}' "$container_id")
        case "$running" in true|false) :;; *) echo "cannot verify database running state" >&2; exit 1;; esac
        [ "$running" = true ] || break
        [ "$stop_attempt" -lt 120 ] || { echo "legacy seekdb did not stop within 120s; container retained" >&2; exit 1; }
        sleep 1
        stop_attempt=$((stop_attempt + 1))
      done
    else
      docker stop --time 120 "$AIV_SEEKDB_CONTAINER" >/dev/null
    fi
  fi
  if [ "$pending" = true ]; then
    stopped_state=$(docker inspect --format '{{.State.Status}} {{.State.Running}} {{.State.Restarting}} {{.State.Dead}} {{.State.ExitCode}} {{.State.OOMKilled}}' "$container_id")
    [ "$stopped_state" = 'exited false false false 0 false' ] || { echo "seekdb did not stop cleanly; refusing offline initialization ($stopped_state)" >&2; exit 1; }
    rm "$stop_record"
  fi
else
  # Only a successful listing can establish absence after inspect fails.
  containers=$(docker container ls --all --format '{{.Names}}' --filter "name=$AIV_SEEKDB_CONTAINER")
  if printf '%s\n' "$containers" | grep -Fx "$AIV_SEEKDB_CONTAINER" >/dev/null; then
    echo "database container exists but could not be inspected" >&2; exit 1
  fi
  rm -f "$stop_record"
fi
initializer="$AIV_SEEKDB_CONTAINER-initialize"
created=false
init_network=
cleanup() {
  if [ "$created" = true ]; then
    docker stop --time 120 "$initializer" >/dev/null 2>&1 || true
    docker rm -f "$initializer" >/dev/null 2>&1 || true
  fi
  if [ -n "$init_network" ]; then docker network rm "$init_network" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if docker network inspect "$initializer-net" >/dev/null 2>&1; then
  echo "seekdb initialization network already exists; refusing concurrent initialization" >&2; exit 1
fi
# seekdb requires an Ethernet interface even for loopback SQL. A dedicated
# internal network gives it an interface without an external route or peers.
init_network=$(docker network create --internal "$initializer-net")
docker create --name "$initializer" --network "$init_network" --memory "$AIV_SEEKDB_CONTAINER_MEMORY" --ulimit nofile=65536:65536 \
  -v "$AIV_SEEKDB_VOLUME:/var/lib/seekdb" \
  --mount "type=bind,source=$AIV_SEEKDB_SCRIPT,target=/opt/aivision/seekdb.sh,readonly" \
  -e AIV_SEEKDB_PASSWORD -e AIV_SEEKDB_MEMORY_BUDGET -e AIV_SEEKDB_CPU -e AIV_SEEKDB_DATA -e AIV_SEEKDB_LOG \
  --entrypoint /bin/sh "$AIV_SEEKDB_IMAGE" /opt/aivision/seekdb.sh initialize >/dev/null
created=true
docker start -a "$initializer"
code=$(docker inspect --format '{{.State.ExitCode}}' "$initializer")
[ "$code" = 0 ] || { echo "seekdb offline initialization failed" >&2; exit 1; }
`
