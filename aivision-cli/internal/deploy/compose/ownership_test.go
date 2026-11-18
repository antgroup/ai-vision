package compose

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestIdentityRetainsPythonSchemaWithoutCredentialsAndRejectsChangedStorage(t *testing.T) {
	_, raw := lifecycleFixture(t)
	_, identity, err := parseComposeConfig([]byte(encodedFixture(t, raw)))
	if err != nil {
		t.Fatal(err)
	}
	encoded := encodedFixture(t, identity)
	for _, secret := range []string{"private_password", "OB_TENANT_PASSWORD"} {
		if strings.Contains(encoded, secret) {
			t.Fatal("identity leaked credentials")
		}
	}
	path := filepath.Join(t.TempDir(), "identity.json")
	if err := os.WriteFile(path, []byte(encoded), 0600); err != nil {
		t.Fatal(err)
	}
	if err := verifyIdentity(path, identity); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(*deploymentIdentity){
		func(value *deploymentIdentity) { value.Backend = "oceanbase" },
		func(value *deploymentIdentity) { value.Managed = false },
		func(value *deploymentIdentity) { value.HistoryStart = "2026-10-02T00:00:00Z" },
		func(value *deploymentIdentity) { value.Volumes = map[string]string{"database-data-0": "replacement"} },
		func(value *deploymentIdentity) {
			value.MetadataMounts = map[string]string{"data-backend": "/replacement"}
		},
		func(value *deploymentIdentity) {
			value.Database = map[string]string{"OB_HOST": "foreign", "OB_HOST_PORT": "3881", "OB_USER": "root"}
		},
	} {
		changed := identity
		change(&changed)
		if err := verifyIdentity(path, changed); err == nil {
			t.Fatal("identity/storage change accepted")
		}
	}
}

func TestInvalidProductIdentityRejectedWithoutDocker(t *testing.T) {
	for _, change := range []func(map[string]any){
		func(raw map[string]any) { raw["x-aivision"].(map[string]any)["backend"] = "mysql" },
		func(raw map[string]any) { raw["x-aivision"].(map[string]any)["namespace"] = "foreign" },
		func(raw map[string]any) { raw["x-aivision"].(map[string]any)["state_dir"] = "relative" },
		func(raw map[string]any) { raw["x-aivision"].(map[string]any)["state_dir"] = "/" },
		func(raw map[string]any) { raw["x-aivision"].(map[string]any)["history_start"] = "" },
		func(raw map[string]any) {
			raw["x-aivision"].(map[string]any)["history_start"] = "2026-10-01T00:00:00+08:00"
		},
		func(raw map[string]any) { delete(raw["x-aivision"].(map[string]any), "managed") },
		func(raw map[string]any) {
			raw["volumes"].(map[string]any)["database-data-0"].(map[string]any)["labels"] = map[string]string{}
		},
	} {
		_, raw := lifecycleFixture(t)
		change(raw)
		if _, _, err := parseComposeConfig([]byte(encodedFixture(t, raw))); err == nil {
			t.Fatal("invalid deployment identity accepted")
		}
	}
}

func TestLegacyStateAndUnknownMetadataRejectedBeforeDocker(t *testing.T) {
	_, raw := lifecycleFixture(t)
	config, current, err := parseComposeConfig([]byte(encodedFixture(t, raw)))
	if err != nil {
		t.Fatal(err)
	}
	controller := lifecycleController{config: config, identity: current, run: func([]string, bool) (dockerResult, error) {
		t.Fatal("Docker reached after local ownership rejection")
		return dockerResult{}, nil
	}}
	if err := os.MkdirAll(current.StateDir, 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"meta.json", "datastore-backend"} {
		path := filepath.Join(current.StateDir, name)
		if err := os.WriteFile(path, []byte("legacy"), 0600); err != nil {
			t.Fatal(err)
		}
		if err := controller.checkOwnership(); err == nil {
			t.Fatal("legacy state adopted")
		}
		if err := os.Remove(path); err != nil {
			t.Fatal(err)
		}
	}
	metadata := current.MetadataMounts["data-backend"]
	if err := os.MkdirAll(metadata, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(metadata, "signoz.db"), []byte("retained metadata"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := controller.checkOwnership(); err == nil {
		t.Fatal("nonempty metadata adopted")
	}
}

func TestMetadataOwnerSurvivesAndRejectsMovedState(t *testing.T) {
	_, raw := lifecycleFixture(t)
	config, current, err := parseComposeConfig([]byte(encodedFixture(t, raw)))
	if err != nil {
		t.Fatal(err)
	}
	controller := lifecycleController{config: config, identity: current}
	if err := controller.reserveMetadata(); err != nil {
		t.Fatal(err)
	}
	metadata := current.MetadataMounts["data-backend"]
	marker := filepath.Join(metadata, metadataOwnerName)
	before, err := os.ReadFile(marker)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(metadata, "signoz.db"), []byte("retained"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := controller.reserveMetadata(); err != nil {
		t.Fatal(err)
	}
	after, _ := os.ReadFile(marker)
	if string(before) != string(after) {
		t.Fatal("metadata ownership rewritten")
	}
	controller.identity.StateDir = filepath.Join(filepath.Dir(current.StateDir), "other")
	if err := controller.reserveMetadata(); err == nil {
		t.Fatal("metadata accepted from moved state")
	}
}

func TestOrphanVolumesAndRenamedForeignWritersCannotBeAdopted(t *testing.T) {
	for _, orphan := range []bool{true, false} {
		_, raw := lifecycleFixture(t)
		config, current, err := parseComposeConfig([]byte(encodedFixture(t, raw)))
		if err != nil {
			t.Fatal(err)
		}
		controller := lifecycleController{config: config, identity: current}
		controller.run = func(args []string, capture bool) (dockerResult, error) {
			if args[0] == "volume" && args[1] == "ls" {
				if orphan {
					return dockerResult{stdout: "aivforge-seekdb-data-sample\n"}, nil
				}
				return dockerResult{}, nil
			}
			if args[0] == "volume" && args[1] == "inspect" {
				return dockerResult{stdout: `[{"Name":"aivforge-seekdb-data-sample","Labels":null}]`}, nil
			}
			if args[0] == "ps" {
				return dockerResult{stdout: "a1\n"}, nil
			}
			if args[0] == "container" {
				return dockerResult{stdout: `[{"Id":"a1","Config":{"Labels":{"com.docker.compose.project":"foreign"}},"Mounts":[{"Type":"volume","Name":"aivforge-seekdb-data-sample"}]}]`}, nil
			}
			t.Fatalf("unexpected command %v", args)
			return dockerResult{}, nil
		}
		if err := controller.checkOwnership(); err == nil {
			t.Fatalf("foreign storage accepted orphan=%v", orphan)
		}
	}
}

func TestStorageSnapshotRetriesOnlyExactDisappearedContainers(t *testing.T) {
	for _, message := range []string{"permission denied", "", "No such container: b2", "No such container: a1\npermission denied"} {
		count := 0
		controller := lifecycleController{run: func(args []string, capture bool) (dockerResult, error) {
			count++
			if count == 1 {
				return dockerResult{stdout: "a1\n"}, nil
			}
			return dockerResult{code: 1, stderr: message}, nil
		}}
		if _, err := controller.inspectStorageContainers(); err == nil {
			t.Fatalf("inspection error treated as absence: %q", message)
		}
		if count != 2 {
			t.Fatal("unexpected retry of non-removal failure")
		}
	}
	count := 0
	controller := lifecycleController{run: func(args []string, capture bool) (dockerResult, error) {
		count++
		switch count {
		case 1:
			return dockerResult{stdout: "a1\n"}, nil
		case 2:
			return dockerResult{code: 1, stderr: "Error response from daemon: No such container: a1"}, nil
		case 3:
			return dockerResult{stdout: "b2\n"}, nil
		default:
			return dockerResult{stdout: `[{"Id":"b2","Mounts":[]}]`}, nil
		}
	}}
	containers, err := controller.inspectStorageContainers()
	if err != nil || len(containers) != 1 || containers[0].ID != "b2" || count != 4 {
		t.Fatalf("complete snapshot not retried: %v %v calls=%d", containers, err, count)
	}
}

func TestPasswordChangesRemainOutsideIdentityButEncodedUsernameMustAgree(t *testing.T) {
	_, raw := lifecycleFixture(t)
	_, before, err := parseComposeConfig([]byte(encodedFixture(t, raw)))
	if err != nil {
		t.Fatal(err)
	}
	database := raw["services"].(map[string]any)["database-init"].(map[string]any)["environment"].(map[string]string)
	database["OB_TENANT_PASSWORD"] = "replacement_private_password"
	_, after, err := parseComposeConfig([]byte(encodedFixture(t, raw)))
	if err != nil || encodedFixture(t, before) != encodedFixture(t, after) {
		t.Fatal("credential rotation became storage identity")
	}
	database["OB_USER"] = "root@tenant"
	raw["services"].(map[string]any)["diagnosis"] = map[string]any{"environment": map[string]string{"AGENTLENS_OB_URL": "mysql+pymysql://root%40tenant:private@database:2881/agentlens"}}
	if _, _, err := parseComposeConfig([]byte(encodedFixture(t, raw))); err != nil {
		t.Fatal(err)
	}
	raw["services"].(map[string]any)["diagnosis"].(map[string]any)["environment"].(map[string]string)["AGENTLENS_OB_URL"] = "mysql+pymysql://other:private@database:2881/agentlens"
	if _, _, err := parseComposeConfig([]byte(encodedFixture(t, raw))); err == nil {
		t.Fatal("different SQL and URL username accepted")
	}
}

func TestNamedContainerInspectionPermissionFailureIsNotAbsence(t *testing.T) {
	_, raw := lifecycleFixture(t)
	config, current, err := parseComposeConfig([]byte(encodedFixture(t, raw)))
	if err != nil {
		t.Fatal(err)
	}
	for _, message := range []string{"permission denied", "", "No such container: unrelated"} {
		controller := lifecycleController{config: config, identity: current, run: func(args []string, capture bool) (dockerResult, error) {
			if args[0] == "container" {
				return dockerResult{code: 1, stderr: message}, nil
			}
			return dockerResult{}, nil
		}}
		if err := controller.checkOwnership(); err == nil {
			t.Fatalf("inspection failure became absence %q", message)
		}
	}
}
