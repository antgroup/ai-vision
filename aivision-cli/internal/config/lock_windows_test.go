package config

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestWindowsProfileLockBlocksConcurrentWriter(t *testing.T) {
	t.Setenv(EnvConfigDir, t.TempDir())
	first, err := lock(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer first()

	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	if release, err := lock(ctx); !errors.Is(err, context.DeadlineExceeded) {
		if release != nil {
			release()
		}
		t.Fatalf("a concurrent writer escaped the held lock: %v", err)
	}
	first()
	release, err := lock(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	release()
}
