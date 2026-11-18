//go:build native_distribution

package assets

import (
	"io/fs"
	"strings"
	"testing"
)

func TestNativeDistributionContainsOnlyNativeBootstrapAssets(t *testing.T) {
	err := fs.WalkDir(Files, ".", func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if strings.HasSuffix(path, ".py") {
			t.Errorf("native distribution embeds Python source: %s", path)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"components.json", "bootstrap/oceanbase.sh", "bootstrap/seekdb.sh"} {
		if _, err := Files.ReadFile(name); err != nil {
			t.Fatal(err)
		}
	}
}
