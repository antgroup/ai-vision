package config

import (
	"errors"
	"os"
	"syscall"
	"time"
)

func replaceFile(from, to string) error {
	return retryWindowsReplace(func() error { return os.Rename(from, to) })
}

func retryWindowsReplace(replace func() error) error {
	const sharingViolation = syscall.Errno(32)
	const maxRetries = 20
	for retries := 0; ; retries++ {
		err := replace()
		// An ordinary Windows reader can temporarily prevent replacing
		// the destination even though all profile writers hold config.lock.
		if retries == maxRetries ||
			(!errors.Is(err, sharingViolation) && !errors.Is(err, syscall.ERROR_ACCESS_DENIED)) {
			return err
		}
		time.Sleep(25 * time.Millisecond)
	}
}
