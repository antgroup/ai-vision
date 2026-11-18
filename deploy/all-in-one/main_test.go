package main

import (
	"errors"
	"os/exec"
	"testing"
	"time"
)

func TestInstallSettingsPersistAcrossRestart(t *testing.T) {
	dir := t.TempDir()
	now := time.Date(2026, 11, 13, 2, 3, 4, 0, time.UTC)
	first, err := loadSettings(dir, now)
	if err != nil {
		t.Fatal(err)
	}
	second, err := loadSettings(dir, now.Add(24*time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if first != second || first.TraceHistoryStart != "2026-11-13T00:00:00Z" || first.IngestSecret == first.SignozKey || len(first.SecretKey) < 32 {
		t.Fatalf("persistent settings did not retain distinct secrets and the initial UTC bound")
	}
}

func TestStopReportsSeekdbOwnerExit(t *testing.T) {
	for _, tc := range []struct {
		name string
		code string
		want int
	}{
		{name: "clean", code: "0", want: 0},
		{name: "failed", code: "42", want: 42},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var s stack
			owner, err := s.start("seekdb", t.TempDir(), nil, "sh", "-c", `exit "$1"`, "seekdb-test", tc.code)
			if err != nil {
				t.Fatal(err)
			}
			<-owner.done
			err = s.stop()
			if tc.want == 0 {
				if err != nil {
					t.Fatalf("clean shutdown returned %v", err)
				}
				return
			}
			var exit *exec.ExitError
			if !errors.As(err, &exit) || exit.ExitCode() != tc.want {
				t.Fatalf("shutdown returned %v, want seekdb exit %d", err, tc.want)
			}
		})
	}
}
