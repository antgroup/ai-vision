package sandboxplugin

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"testing"
)

func optionsWithSocketGID(t *testing.T, options Options, gid int) Options {
	t.Helper()
	data, err := json.Marshal(options)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		t.Fatal(err)
	}
	fields["socket_gid_override"] = json.RawMessage(strconv.Itoa(gid))
	data, err = json.Marshal(fields)
	if err != nil || json.Unmarshal(data, &options) != nil {
		t.Fatal("could not decode operator socket GID override")
	}
	return options
}

func TestSocketGIDOverrideControlsSupplementaryGroupAndPreservesUser(t *testing.T) {
	for _, gid := range []int{0, 2147483647} {
		t.Run(strconv.Itoa(gid), func(t *testing.T) {
			f := setup(t)
			f.options = optionsWithSocketGID(t, f.options, gid)
			f.install(t)
			uid, primaryGID, err := ownerIDs(f.options.TokenFile)
			if err != nil {
				t.Fatal(err)
			}
			for _, args := range f.docker.commands {
				if args[0] != "run" {
					continue
				}
				flags := map[string]string{}
				for i, arg := range args[:len(args)-1] {
					if arg == "--user" || arg == "--group-add" {
						flags[arg] = args[i+1]
					}
				}
				if flags["--group-add"] != strconv.Itoa(gid) || flags["--user"] != fmt.Sprintf("%d:%d", uid, primaryGID) {
					t.Fatalf("override must change only the supplementary socket group: %v", flags)
				}
			}
			data, err := os.ReadFile(f.m.statePath())
			if err != nil {
				t.Fatal(err)
			}
			var saved map[string]json.RawMessage
			if json.Unmarshal(data, &saved) != nil || string(saved["socket_gid_override"]) != strconv.Itoa(gid) || string(saved["socket_gid"]) != strconv.Itoa(gid) {
				t.Fatalf("explicit override, including zero, must survive saved state: %s", data)
			}
		})
	}
}

func TestSocketGIDWithoutOverridePreservesHostDerivedGroup(t *testing.T) {
	f := setup(t)
	validated, err := f.options.validate()
	if err != nil {
		t.Fatal(err)
	}
	_, hostGID, err := ownerIDs(f.options.DockerSocket)
	if err != nil || validated.SocketGID != hostGID {
		t.Fatalf("default socket group = %d, host group = %d, err = %v", validated.SocketGID, hostGID, err)
	}
	data, err := json.Marshal(validated)
	if err != nil || strings.Contains(string(data), "socket_gid_override") {
		t.Fatalf("default options must preserve the legacy saved-state shape: %s, %v", data, err)
	}
}

func TestSocketGIDOverrideRejectsOutOfRangeBeforeDocker(t *testing.T) {
	for _, gid := range []int{-1, 2147483648} {
		t.Run(strconv.Itoa(gid), func(t *testing.T) {
			f := setup(t)
			_, err := f.m.Install(context.Background(), optionsWithSocketGID(t, f.options, gid))
			if err == nil || !strings.Contains(err.Error(), "--docker-socket-gid") || !strings.Contains(err.Error(), "2147483647") {
				t.Fatalf("out-of-range override must fail with its supported bounds: %v", err)
			}
			if len(f.docker.commands) != 0 {
				t.Fatal("Docker was invoked for an invalid socket GID")
			}
		})
	}
}

func TestSocketGIDOverrideCannotChangePendingOrInstalledOptions(t *testing.T) {
	t.Run("pending", func(t *testing.T) {
		f := setup(t)
		validated, err := f.options.validate()
		if err != nil {
			t.Fatal(err)
		}
		if err := privateDirectory(f.m.StateDir); err != nil {
			t.Fatal(err)
		}
		if err := f.m.save(&installation{Options: validated, Name: f.m.name(), Identity: strings.Repeat("a", 32)}); err != nil {
			t.Fatal(err)
		}
		_, err = f.m.Install(context.Background(), optionsWithSocketGID(t, f.options, 0))
		if err == nil || !strings.Contains(err.Error(), "original operator options") || len(f.docker.commands) != 0 {
			t.Fatalf("pending installation accepted different operator options: %v, %v", err, f.docker.commands)
		}
	})
	t.Run("installed", func(t *testing.T) {
		f := setup(t)
		f.install(t)
		commands := len(f.docker.commands)
		_, err := f.m.Install(context.Background(), optionsWithSocketGID(t, f.options, 0))
		if err == nil || !strings.Contains(err.Error(), "already exists") || len(f.docker.commands) != commands {
			t.Fatalf("installed container was changed: %v", err)
		}
	})
}
