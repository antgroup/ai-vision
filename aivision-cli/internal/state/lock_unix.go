//go:build unix

package state

import (
	"errors"
	"fmt"
	"os"
	"syscall"
	"time"
)

// acquireRecordLock uses an advisory file lock, which the kernel releases
// automatically when the process exits. A live holder therefore cannot lose
// its lock because of an old timestamp, and there is no stale lock to reclaim.
func acquireRecordLock(store *recordStore, rel string) (func(), error) {
	// A lock file persists so independent processes always lock the same file.
	f, err := store.root.OpenFile(rel, os.O_CREATE|os.O_EXCL|os.O_RDWR, 0o600)
	if os.IsExist(err) {
		f, err = store.root.OpenFile(rel, os.O_RDWR|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0o600)
	}
	if err != nil {
		return nil, err
	}
	if err := verifyRecordFile(store.root, rel, f); err != nil {
		_ = f.Close()
		return nil, err
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			if err := verifyRecordFile(store.root, rel, f); err != nil {
				_ = f.Close()
				return nil, err
			}
			return func() {
				_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
				_ = f.Close()
			}, nil
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			_ = f.Close()
			return nil, err
		}
		if time.Now().After(deadline) {
			_ = f.Close()
			return nil, fmt.Errorf("timed out waiting for ownership record lock %s", rel)
		}
		time.Sleep(10 * time.Millisecond)
	}
}
