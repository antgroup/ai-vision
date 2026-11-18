package sandboxbootstrap

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const gatewayOwnerLabel = "io.aivision.sandbox.bootstrap"
const gatewayPlanLabel = "io.aivision.sandbox.plan"

func gatewayArgs(plan Plan, state *ownedState, images Images, gid int) []string {
	data := filepath.Join(state.Dir, "gateway-state")
	configPath := filepath.Join(state.Dir, "gateway.toml")
	return []string{"run", "--detach", "--name", plan.Target.GatewayName, "--label", gatewayOwnerLabel + "=" + plan.Target.DeploymentKey, "--label", gatewayPlanLabel + "=" + plan.PlanID, "--label", "io.aivision.sandbox.role=gateway", "--network", state.Host.NetworkID, "--network-alias", plan.Target.GatewayName, "--publish", fmt.Sprintf("127.0.0.1:%d:8080", state.GatewayPort), "--restart", "unless-stopped", "--stop-signal", "SIGTERM", "--user", fmt.Sprintf("%d:%d", os.Getuid(), os.Getgid()), "--group-add", strconv.Itoa(gid), "--security-opt", "no-new-privileges=true", "--cap-drop", "ALL", "--mount", "type=bind,source=" + data + ",target=" + data, "--mount", "type=bind,source=" + state.Host.Socket + ",target=/run/docker.sock", "--mount", "type=bind,source=" + configPath + ",target=" + configPath + ",readonly", "--env", "HOME=" + data, "--env", "XDG_DATA_HOME=" + data, "--env", "OPENSHELL_LOCAL_TLS_DIR=" + filepath.Join(data, "tls"), "--env", "OPENSHELL_ENABLE_MTLS_AUTH=true", "--env", "OPENSHELL_DB_URL=sqlite:" + filepath.Join(data, "gateway.db") + "?mode=rwc", images.Gateway, "--config", configPath}
}
func ensureTLS(ctx context.Context, docker Docker, plan Plan, state *ownedState, images Images) error {
	data := filepath.Join(state.Dir, "gateway-state")
	dir := filepath.Join(data, "tls")
	required := []string{"ca.crt", "ca.key", "server/tls.crt", "server/tls.key", "client/tls.crt", "client/tls.key", "jwt/signing.pem", "jwt/public.pem", "jwt/kid"}
	complete := true
	for _, name := range required {
		if _, err := privateFile(filepath.Join(dir, name)); err != nil {
			if !os.IsNotExist(err) {
				return errors.New("gateway TLS files changed or are not private")
			}
			complete = false
		}
	}
	if !complete {
		if _, err := docker.Run(ctx, "run", "--rm", "--name", plan.Target.GatewayName+"-pki", "--network", "none", "--user", fmt.Sprintf("%d:%d", os.Getuid(), os.Getgid()), "--security-opt", "no-new-privileges=true", "--cap-drop", "ALL", "--mount", "type=bind,source="+data+",target="+data, "--env", "HOME="+data, "--env", "XDG_CONFIG_HOME="+filepath.Join(data, "config"), images.Gateway, "generate-certs", "--output-dir", dir, "--server-san", plan.Target.GatewayName, "--server-san", "127.0.0.1"); err != nil {
			return errors.New("could not generate the owned gateway mTLS and JWT files")
		}
		if err := filepath.WalkDir(data, func(path string, entry os.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if entry.Type()&os.ModeSymlink != 0 {
				return errors.New("unsafe gateway file")
			}
			mode := os.FileMode(0600)
			if entry.IsDir() {
				mode = 0700
			}
			return os.Chmod(path, mode)
		}); err != nil {
			return errors.New("gateway TLS ownership could not be secured")
		}
	}
	for _, name := range required {
		if _, err := privateFile(filepath.Join(dir, name)); err != nil {
			return errors.New("gateway TLS initialization is incomplete; private state was retained")
		}
	}
	raw, _ := privateFile(filepath.Join(dir, "server/tls.crt"))
	block, _ := pem.Decode(raw)
	if block == nil {
		return errors.New("gateway server certificate is invalid")
	}
	certificate, err := x509.ParseCertificate(block.Bytes)
	if err != nil || certificate.VerifyHostname(plan.Target.GatewayName) != nil || certificate.VerifyHostname("127.0.0.1") != nil {
		return errors.New("gateway server certificate does not match its private DNS and localhost endpoints")
	}
	return nil
}
func verifyGateway(resource container, plan Plan, state *ownedState, images Images) error {
	bad := errors.New("gateway ownership, image, network or localhost exposure changed; owned resources were retained")
	if resource.Config.Labels[gatewayOwnerLabel] != plan.Target.DeploymentKey || resource.Config.Labels[gatewayPlanLabel] != plan.PlanID || resource.Config.Labels["io.aivision.sandbox.role"] != "gateway" || resource.Config.Image != images.Gateway || (state.GatewayID != "" && resource.ID != state.GatewayID) || resource.HostConfig.Privileged || len(resource.HostConfig.CapAdd) > 0 {
		return bad
	}
	if resource.Config.User != fmt.Sprintf("%d:%d", os.Getuid(), os.Getgid()) || len(resource.Config.Cmd) != 2 || resource.Config.Cmd[0] != "--config" || resource.Config.Cmd[1] != filepath.Join(state.Dir, "gateway.toml") || len(resource.HostConfig.GroupAdd) != 1 || resource.HostConfig.GroupAdd[0] != strconv.Itoa(state.SocketGID) || len(resource.HostConfig.CapDrop) != 1 || resource.HostConfig.CapDrop[0] != "ALL" || len(resource.HostConfig.SecurityOpt) != 1 || (resource.HostConfig.SecurityOpt[0] != "no-new-privileges=true" && resource.HostConfig.SecurityOpt[0] != "no-new-privileges:true") {
		return bad
	}
	data := filepath.Join(state.Dir, "gateway-state")
	for key, want := range map[string]string{"HOME": data, "XDG_DATA_HOME": data, "OPENSHELL_LOCAL_TLS_DIR": filepath.Join(data, "tls"), "OPENSHELL_ENABLE_MTLS_AUTH": "true", "OPENSHELL_DB_URL": "sqlite:" + filepath.Join(data, "gateway.db") + "?mode=rwc"} {
		if envValue(resource.Config.Env, key) != want {
			return bad
		}
	}
	for _, entry := range resource.Config.Env {
		if strings.HasPrefix(entry, "OPENSHELL_DISABLE_TLS=") && entry != "OPENSHELL_DISABLE_TLS=false" {
			return bad
		}
	}
	if len(resource.NetworkSettings.Networks) != 1 {
		return bad
	}
	networkOK := false
	for _, network := range resource.NetworkSettings.Networks {
		if network.NetworkID == state.Host.NetworkID {
			for _, alias := range network.Aliases {
				networkOK = networkOK || alias == plan.Target.GatewayName
			}
		}
	}
	if !networkOK {
		return bad
	}
	ports := resource.HostConfig.PortBindings["8080/tcp"]
	if len(ports) != 1 || ports[0].HostIP != "127.0.0.1" || ports[0].HostPort != strconv.Itoa(state.GatewayPort) || len(resource.HostConfig.PortBindings) != 1 {
		return bad
	}
	expected := map[string]struct {
		destination string
		rw          bool
	}{filepath.Join(state.Dir, "gateway-state"): {filepath.Join(state.Dir, "gateway-state"), true}, state.Host.Socket: {"/run/docker.sock", true}, filepath.Join(state.Dir, "gateway.toml"): {filepath.Join(state.Dir, "gateway.toml"), false}}
	if len(resource.Mounts) != len(expected) {
		return bad
	}
	for _, mount := range resource.Mounts {
		want, ok := expected[mount.Source]
		if !ok || mount.Destination != want.destination || mount.RW != want.rw {
			return bad
		}
	}
	return nil
}
func startGateway(ctx context.Context, docker Docker, plan Plan, state *ownedState, images Images) error {
	raw, err := docker.Run(ctx, "container", "ls", "--all", "--filter", "name=^/"+plan.Target.GatewayName+"$", "--format", "{{.ID}}")
	if err != nil {
		return errors.New("could not inspect the owned gateway name")
	}
	if strings.TrimSpace(string(raw)) == "" {
		if state.GatewayID != "" {
			return errors.New("the saved owned gateway was removed; state was retained")
		}
		created, err := docker.Run(ctx, gatewayArgs(plan, state, images, state.SocketGID)...)
		if err != nil {
			return errors.New("gateway startup failed; owned state was retained")
		}
		id := strings.TrimSpace(string(created))
		if !fullID.MatchString(id) {
			return errors.New("could not verify newly created gateway identity")
		}
		state.GatewayID = id
		if err = state.save(); err != nil {
			return err
		}
	}
	resource, err := inspect(ctx, docker, plan.Target.GatewayName)
	if err != nil {
		return err
	}
	if err = verifyGateway(resource, plan, state, images); err != nil {
		return err
	}
	if state.GatewayID == "" {
		state.GatewayID = resource.ID
		if err = state.save(); err != nil {
			return err
		}
	}
	if !resource.State.Running {
		if _, err = docker.Run(ctx, "start", state.GatewayID); err != nil {
			return errors.New("could not restart the exact owned gateway")
		}
	}
	return waitGateway(ctx, plan, state)
}
func waitGateway(ctx context.Context, plan Plan, state *ownedState) error {
	dir := filepath.Join(state.Dir, "gateway-state/tls")
	ca, err := privateFile(filepath.Join(dir, "ca.crt"))
	if err != nil {
		return errors.New("gateway CA unavailable")
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(ca) {
		return errors.New("gateway CA is invalid")
	}
	cert, err := privateFile(filepath.Join(dir, "client/tls.crt"))
	if err != nil {
		return errors.New("gateway client certificate unavailable")
	}
	key, err := privateFile(filepath.Join(dir, "client/tls.key"))
	if err != nil {
		return errors.New("gateway client key unavailable")
	}
	pair, err := tls.X509KeyPair(cert, key)
	if err != nil {
		return errors.New("gateway client mTLS identity is invalid")
	}
	timeout, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	for {
		dialer := tls.Dialer{NetDialer: &net.Dialer{Timeout: 2 * time.Second}, Config: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: pool, Certificates: []tls.Certificate{pair}, ServerName: "127.0.0.1"}}
		connection, err := dialer.DialContext(timeout, "tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(state.GatewayPort)))
		if err == nil {
			connection.Close()
			return nil
		}
		select {
		case <-timeout.Done():
			return errors.New("gateway authenticated localhost startup could not be verified; inspect its retained owned state")
		case <-time.After(time.Second):
		}
	}
}
