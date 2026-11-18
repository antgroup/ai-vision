//go:build linux || darwin

package config

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

func lock(ctx context.Context) (func(), error) {
	if err := os.MkdirAll(Dir(), dirPerms); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(filepath.Join(Dir(), "config.lock"), os.O_CREATE|os.O_RDWR, filePerms)
	if err != nil {
		return nil, err
	}
	for {
		err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return func() { _ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN); _ = f.Close() }, nil
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			f.Close()
			return nil, err
		}
		select {
		case <-ctx.Done():
			f.Close()
			return nil, ctx.Err()
		case <-time.After(25 * time.Millisecond):
		}
	}
}
