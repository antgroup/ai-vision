//go:build linux || darwin

package sandboxplugin

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

func lock(ctx context.Context, directory string) (func(), error) {
	if err := privateDirectory(directory); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(filepath.Join(directory, "installation.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	for {
		err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return func() { _ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN); _ = f.Close() }, nil
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			_ = f.Close()
			return nil, err
		}
		select {
		case <-ctx.Done():
			_ = f.Close()
			return nil, ctx.Err()
		case <-time.After(25 * time.Millisecond):
		}
	}
}

func ownerIDs(path string) (int, int, error) {
	info, err := os.Stat(path)
	if err != nil {
		return 0, 0, err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return 0, 0, errors.New("operator file ownership cannot be determined")
	}
	return int(stat.Uid), int(stat.Gid), nil
}
