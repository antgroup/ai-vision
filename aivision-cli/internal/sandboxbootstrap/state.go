//go:build linux || darwin

package sandboxbootstrap

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

const workerPolicy = `{"version":1,"process":{"run_as_user":"1000","run_as_group":"1000"},"landlock":{"compatibility":"hard_requirement"},"filesystem_policy":{"include_workdir":true,"read_only":["/usr","/lib","/bin","/etc","/proc","/dev/urandom","/var/log","/app"],"read_write":["/work","/tmp","/dev/null"]},"network_policies":{}}` + "\n"

type ownedState struct {
	SchemaVersion int    `json:"schema_version"`
	PlanID        string `json:"plan_id"`
	Host          Host   `json:"host"`
	GatewayPort   int    `json:"gateway_port"`
	PluginPort    int    `json:"plugin_port"`
	SocketGID     int    `json:"socket_gid"`
	GatewayID     string `json:"gateway_id,omitempty"`
	Dir           string `json:"-"`
}

func privateDirectory(path string) error {
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		if err = os.Mkdir(path, 0700); err != nil {
			return errors.New("could not create private setup directory")
		}
		info, err = os.Lstat(path)
	}
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0077 != 0 {
		return errors.New("setup directories must be real owner-only directories (0700)")
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || int(stat.Uid) != os.Getuid() {
		return errors.New("setup directory belongs to another user")
	}
	return nil
}
func privateFile(path string) ([]byte, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || !ok || int(stat.Uid) != os.Getuid() || info.Size() > maxPlanBytes {
		return nil, errors.New("setup files must be real owner-only files (0600)")
	}
	return os.ReadFile(path)
}
func createFixed(path string, raw []byte, secret bool) error {
	existing, err := privateFile(path)
	if err == nil {
		if !secret && string(existing) != string(raw) {
			return errors.New("existing owned configuration changed; original files were retained")
		}
		if secret && (len(existing) < 32 || len(existing) > 4096 || strings.ContainsAny(string(existing), "\n\r \t")) {
			return errors.New("saved service credential is invalid")
		}
		return nil
	}
	if !os.IsNotExist(err) {
		return errors.New("refusing an unsafe or changed setup file")
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return errors.New("could not exclusively create private setup file")
	}
	defer file.Close()
	if _, err = file.Write(raw); err != nil {
		return errors.New("could not write private setup file")
	}
	return file.Sync()
}
func randomPort() (int, error) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, errors.New("could not reserve an available localhost port")
	}
	defer listener.Close()
	return listener.Addr().(*net.TCPAddr).Port, nil
}
func (state *ownedState) save() error {
	path := filepath.Join(state.Dir, "setup.json")
	if _, err := privateFile(path); err != nil && !os.IsNotExist(err) {
		return errors.New("refusing changed setup ownership state")
	}
	raw, _ := json.MarshalIndent(state, "", "  ")
	if err := config.AtomicWrite(path, raw); err != nil {
		return errors.New("could not save private setup state")
	}
	return nil
}
func openOwned(ctx context.Context, root string, plan Plan, host Host) (*ownedState, func(), error) {
	empty := func() {}
	absolute, err := filepath.Abs(root)
	if err != nil || absolute == "/" || strings.ContainsAny(absolute, ",\n\r") {
		return nil, empty, errors.New("invalid private setup root")
	}
	// The configured base is operator-owned; reject symlinks rather than silently
	// moving secrets into a different directory. Only new descendants are created.
	if _, err = os.Lstat(absolute); os.IsNotExist(err) {
		if err = os.MkdirAll(filepath.Dir(absolute), 0700); err != nil {
			return nil, empty, errors.New("could not create setup base")
		}
	}
	if err = privateDirectory(absolute); err != nil {
		return nil, empty, err
	}
	dir := absolute
	for _, part := range []string{"deployments", plan.Target.DeploymentKey, "plugins", "openshell"} {
		dir = filepath.Join(dir, part)
		if err = privateDirectory(dir); err != nil {
			return nil, empty, err
		}
	}
	lockPath := filepath.Join(dir, "setup.lock")
	descriptor, err := syscall.Open(lockPath, syscall.O_RDWR|syscall.O_CREAT|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, empty, errors.New("could not acquire private setup lock")
	}
	file := os.NewFile(uintptr(descriptor), lockPath)
	if _, err = privateFile(lockPath); err != nil {
		file.Close()
		return nil, empty, errors.New("unsafe setup lock")
	}
	for {
		err = syscall.Flock(descriptor, syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			break
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			file.Close()
			return nil, empty, errors.New("could not acquire setup lock")
		}
		select {
		case <-ctx.Done():
			file.Close()
			return nil, empty, errors.New("setup canceled while waiting for its lock")
		case <-time.After(25 * time.Millisecond):
		}
	}
	unlock := func() { syscall.Flock(descriptor, syscall.LOCK_UN); file.Close() }
	state := &ownedState{SchemaVersion: 1, PlanID: plan.PlanID, Host: host, Dir: dir}
	raw, err := privateFile(filepath.Join(dir, "setup.json"))
	if err == nil {
		if decodeStrict(raw, state) != nil || state.SchemaVersion != 1 || state.PlanID != plan.PlanID || !reflect.DeepEqual(state.Host, host) || state.GatewayPort < 1 || state.GatewayPort > 65535 || state.PluginPort < 1 || state.PluginPort > 65535 || state.GatewayPort == state.PluginPort || (state.GatewayID != "" && !fullID.MatchString(state.GatewayID)) {
			unlock()
			return nil, empty, errors.New("saved setup belongs to a different plan, daemon or deployment; owned resources were retained")
		}
		state.Dir = dir
		return state, unlock, nil
	}
	if !os.IsNotExist(err) {
		unlock()
		return nil, empty, errors.New("refusing unsafe setup ownership metadata")
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		unlock()
		return nil, empty, err
	}
	for _, entry := range entries {
		if entry.Name() != "setup.lock" {
			unlock()
			return nil, empty, errors.New("unowned files already occupy the setup directory")
		}
	}
	state.GatewayPort, err = randomPort()
	if err == nil {
		state.PluginPort, err = randomPort()
	}
	if err == nil && state.PluginPort == state.GatewayPort {
		state.PluginPort, err = randomPort()
	}
	if err != nil {
		unlock()
		return nil, empty, err
	}
	if err = state.save(); err != nil {
		unlock()
		return nil, empty, err
	}
	return state, unlock, nil
}
func (state *ownedState) configure() error {
	for _, subdir := range []string{"journal", "installation", "gateway-state"} {
		if err := privateDirectory(filepath.Join(state.Dir, subdir)); err != nil {
			return err
		}
	}
	var secret [32]byte
	if _, err := rand.Read(secret[:]); err != nil {
		return errors.New("could not generate private service credential")
	}
	if err := createFixed(filepath.Join(state.Dir, "plugin-token"), []byte("aiv-sandbox-"+base64.RawURLEncoding.EncodeToString(secret[:])), true); err != nil {
		return err
	}
	return createFixed(filepath.Join(state.Dir, "policy.json"), []byte(workerPolicy), false)
}
func (state *ownedState) gatewayConfig(plan Plan, images Images) []byte {
	data := filepath.Join(state.Dir, "gateway-state")
	tlsDir := filepath.Join(data, "tls")
	q := strconv.Quote
	content := "[openshell]\nversion = 2\n[openshell.gateway]\nname = " + q(plan.Target.GatewayName) + "\nbind_address = \"0.0.0.0:8080\"\ncompute_driver = \"docker\"\nlog_level = \"info\"\ndisable_tls = false\nguest_tls_ca = " + q(filepath.Join(tlsDir, "ca.crt")) + "\n[openshell.gateway.tls]\ncert_path = " + q(filepath.Join(tlsDir, "server/tls.crt")) + "\nkey_path = " + q(filepath.Join(tlsDir, "server/tls.key")) + "\nclient_ca_path = " + q(filepath.Join(tlsDir, "ca.crt")) + "\n[openshell.gateway.gateway_jwt]\nsigning_key_path = " + q(filepath.Join(tlsDir, "jwt/signing.pem")) + "\npublic_key_path = " + q(filepath.Join(tlsDir, "jwt/public.pem")) + "\nkid_path = " + q(filepath.Join(tlsDir, "jwt/kid")) + "\ngateway_id = " + q(plan.Target.GatewayName) + "\n[openshell.drivers.docker]\nsocket_path = \"/run/docker.sock\"\ndefault_image = " + q(images.Worker) + "\nsandbox_runtime_image = " + q(images.Runtime) + "\nsupervisor_image = " + q(images.Supervisor) + "\nimage_pull_policy = \"never\"\nsandbox_label = " + q(plan.Target.ServiceName) + "\nsupervisor_network_mode = \"host\"\ngrpc_endpoint = " + q("https://127.0.0.1:"+strconv.Itoa(state.GatewayPort)) + "\nenable_bind_mounts = false\napp_armor_profile = \"Unconfined\"\n"
	return []byte(content)
}
