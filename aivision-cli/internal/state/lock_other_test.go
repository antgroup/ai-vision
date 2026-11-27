//go:build !unix

package state

import (
	"os"
	"testing"
	"time"
)

func TestLockDirectoryBlocksIndependentRecordStore(t *testing.T) {
	s := NewState(t.TempDir(), "aivision")
	first, err := s.openRecordStore(true)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	second, err := s.openRecordStore(true)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	lockPath := s.lockFile()
	rel, err := first.rel(lockPath)
	if err != nil {
		t.Fatal(err)
	}
	release, err := acquireRecordLock(first, rel)
	if err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-time.Hour)
	if err := os.Chtimes(lockPath, old, old); err != nil {
		t.Fatal(err)
	}

	acquired := make(chan struct{})
	errCh := make(chan error, 1)
	go func() {
		rel, err := second.rel(lockPath)
		if err != nil {
			errCh <- err
			return
		}
		release, err := acquireRecordLock(second, rel)
		if err != nil {
			errCh <- err
			return
		}
		close(acquired)
		release()
	}()
	select {
	case <-acquired:
		t.Fatal("second record store reclaimed a live lock directory")
	case <-time.After(250 * time.Millisecond):
	}
	release()
	select {
	case <-acquired:
	case err := <-errCh:
		t.Fatalf("second record store failed to acquire the released lock: %v", err)
	case <-time.After(2 * time.Second):
		t.Fatal("second record store did not acquire the released lock")
	}
}
