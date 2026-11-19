package config

import (
	"context"
	"os"
	"path/filepath"
	"syscall"
	"time"
	"unsafe"
)

var lockFileEx = syscall.NewLazyDLL("kernel32.dll").NewProc("LockFileEx")

func lock(ctx context.Context) (func(), error) {
	if err := os.MkdirAll(Dir(), dirPerms); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(filepath.Join(Dir(), "config.lock"), os.O_CREATE|os.O_RDWR, filePerms)
	if err != nil {
		return nil, err
	}
	const (
		failImmediately = 1
		exclusiveLock   = 2
		lockViolation   = syscall.Errno(33)
	)
	for {
		var region syscall.Overlapped
		ok, _, lockErr := lockFileEx.Call(file.Fd(), failImmediately|exclusiveLock, 0, 1, 0, uintptr(unsafe.Pointer(&region)))
		if ok != 0 {
			// Windows releases a byte range lock when its file handle closes.
			return func() { _ = file.Close() }, nil
		}
		if lockErr != lockViolation {
			file.Close()
			return nil, lockErr
		}
		select {
		case <-ctx.Done():
			file.Close()
			return nil, ctx.Err()
		case <-time.After(25 * time.Millisecond):
		}
	}
}
