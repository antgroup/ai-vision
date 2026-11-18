package sandboxplugin

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

type networkDocker struct {
	*fakeDocker
	network, alias string
	changed        bool
}

func (d *networkDocker) Run(ctx context.Context, args ...string) ([]byte, error) {
	if args[0] == "run" {
		for i, arg := range args {
			if arg == "--network" {
				d.network = args[i+1]
			}
			if arg == "--network-alias" {
				d.alias = args[i+1]
			}
		}
	}
	raw, err := d.fakeDocker.Run(ctx, args...)
	if err == nil && len(args) > 1 && args[0] == "container" && args[1] == "inspect" {
		var object map[string]any
		json.Unmarshal(raw, &object)
		network := d.network
		if d.changed {
			network = strings.Repeat("b", 64)
		}
		object["NetworkSettings"].(map[string]any)["Networks"] = map[string]any{"deployment": map[string]any{"NetworkID": network, "Aliases": []string{d.alias}}}
		return json.Marshal(object)
	}
	return raw, err
}
func TestBootstrapNetworkAliasIsPersistedAndReadBack(t *testing.T) {
	f := setup(t)
	docker := &networkDocker{fakeDocker: f.docker}
	f.m.Docker = docker
	f.options.Network = strings.Repeat("a", 64)
	f.options.NetworkAlias = "aiv-openshell-owned"
	f.install(t)
	if docker.network != f.options.Network || docker.alias != f.options.NetworkAlias {
		t.Fatal("plugin did not join the pinned deployment network")
	}
	saved, err := f.m.load()
	if err != nil || saved.Network != f.options.Network || saved.NetworkAlias != f.options.NetworkAlias {
		t.Fatalf("network ownership not persisted: %#v %v", saved, err)
	}
	docker.changed = true
	if _, err = f.m.Status(context.Background()); err == nil {
		t.Fatal("status accepted changed network identity")
	}
}
