//go:build !unix

package state

import (
	"fmt"
	"os"
	"time"
)

// acquireRecordLock falls back to an atomic lock directory on platforms
// without flock. The directory's age does not identify a live holder.
func acquireRecordLock(store *recordStore, rel string) (func(), error) {
	deadline := time.Now().Add(5 * time.Second)
	for {
		err := store.root.Mkdir(rel, 0o700)
		if err == nil {
			info, err := store.root.Lstat(rel)
			if err != nil {
				return nil, err
			}
			if !info.IsDir() {
				return nil, fmt.Errorf("ownership lock %s is not a directory", rel)
			}
			return func() {
				current, err := store.root.Lstat(rel)
				if err == nil && os.SameFile(info, current) {
					_ = store.root.Remove(rel)
				}
			}, nil
		}
		if !os.IsExist(err) {
			return nil, err
		}
		if time.Now().After(deadline) {
			return nil, fmt.Errorf("timed out waiting for ownership record lock %s", rel)
		}
		time.Sleep(10 * time.Millisecond)
	}
}
