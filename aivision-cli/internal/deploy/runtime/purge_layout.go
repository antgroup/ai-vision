package runtime

import (
	"fmt"

	"github.com/antgroup/ai-vision/aivision-cli/internal/pathguard"
)

// VerifyStatePurge checks the entire directory that --purge will remove.
// Source and state locations are independently configurable in a blueprint.
func (a *App) VerifyStatePurge() error {
	if err := a.State.VerifyPurgeTarget(); err != nil {
		return fmt.Errorf("remove state: %w", err)
	}
	check := func(path string) error {
		if path == "" {
			return nil
		}
		inside, err := pathguard.Contains(a.State.Root, path)
		if err != nil {
			return fmt.Errorf("check source path %s before state purge: %w", path, err)
		}
		if inside {
			return fmt.Errorf("cannot purge state directory %s: it contains source path %s", a.State.Root, path)
		}
		return nil
	}
	if err := check(a.SourceRoot()); err != nil {
		return err
	}
	for _, component := range a.BP.Components {
		if err := check(a.RepoDir(component)); err != nil {
			return err
		}
	}
	return nil
}

func (a *App) verifyCheckoutPurge(path string) error {
	inside, err := pathguard.Contains(path, a.State.Root)
	if err != nil {
		return fmt.Errorf("check checkout %s before removal: %w", path, err)
	}
	if inside {
		return fmt.Errorf("cannot purge checkout %s: it contains the deployment state directory %s", path, a.State.Root)
	}
	return nil
}
