package sandboxplugin

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

// These fixtures exercise the Docker process boundary without a daemon or image.
func pullFixture(t *testing.T, body string) NativeDocker {
	t.Helper()
	path := filepath.Join(t.TempDir(), "docker")
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body), 0700); err != nil {
		t.Fatal(err)
	}
	return NativeDocker{Bin: path}
}

func TestNativeDockerPullReportsOnlyValidatedLayerProgress(t *testing.T) {
	docker := pullFixture(t, `
test "$1" = pull && test "$2" = 'registry.example/plugin:1' || exit 2
printf 'auth secret-marker\n'
printf 'abcdef012345: Pulling fs layer\n' >&2
printf 'abcdef012345: Downloading [====>    ] 1.25MB/20MB\r'
printf 'abcdef012345: Download complete\n'
printf 'abcdef012345: Extracting [=======> ] 20MB/20MB\n' >&2
printf 'abcdef012345: Pull complete\n'
printf 'Status: Downloaded newer image for secret-marker\n'
`)
	var events []ImageProgress
	err := docker.Pull(context.Background(), "registry.example/plugin:1", func(event ImageProgress) {
		events = append(events, event)
	})
	if err != nil {
		t.Fatal(err)
	}
	// Streams are merged as they arrive; stdout/stderr ordering is not promised.
	want := map[ImageProgress]bool{
		{Layer: "abcdef012345", Status: "Pulling fs layer"}:                              true,
		{Layer: "abcdef012345", Status: "Downloading", Current: "1.25MB", Total: "20MB"}: true,
		{Layer: "abcdef012345", Status: "Download complete"}:                             true,
		{Layer: "abcdef012345", Status: "Extracting", Current: "20MB", Total: "20MB"}:    true,
		{Layer: "abcdef012345", Status: "Pull complete"}:                                 true,
	}
	if len(events) != len(want) {
		t.Fatalf("unexpected events: %#v", events)
	}
	for _, event := range events {
		if !want[event] {
			t.Fatalf("unexpected event: %#v", event)
		}
		delete(want, event)
	}
}

func TestPullProgressRejectsUntrustedFramesAndRecoversAfterOversize(t *testing.T) {
	var got []ImageProgress
	writer := newImageProgressWriter(func(event ImageProgress) { got = append(got, event) })
	input := []string{
		"abcdef012345: Downloading secret-marker\n",
		"abcdef012345: Pull complete secret-marker\n",
		"abcdef012345: Downloading [====>] secret-marker/1MB\n",
		"abcdef012345: Downloading [====>] 1MB/2MB secret-marker\n",
		"abcdef012345: \x1b[31mPull complete\n",
		"abcdef012345: Downloading [====>] 1MB/2MB\x1b[2K\n",
		"abcdef012345: Download complete\x00secret-marker\n",
		"abcdef012345: Waiting" + strings.Repeat(" ", 100000) + "secret-marker\n",
		"abcdef012345: Already exists\r",
		"abcdef012345: Waiting", // An EOF-terminated valid frame is also useful.
	}
	for _, part := range input {
		for i := 0; i < len(part); i += 7 {
			end := min(i+7, len(part))
			if n, err := writer.Write([]byte(part[i:end])); err != nil || n != end-i {
				t.Fatalf("parser prevented draining Docker output: %d, %v", n, err)
			}
		}
	}
	writer.flush()
	want := []ImageProgress{{Layer: "abcdef012345", Status: "Already exists"}, {Layer: "abcdef012345", Status: "Waiting"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("untrusted or missing output: %#v", got)
	}
}

func TestPullProgressHandlesConcurrentStreams(t *testing.T) {
	var got []ImageProgress
	writer := newImageProgressWriter(func(event ImageProgress) { got = append(got, event) })
	var workers sync.WaitGroup
	for i := range 20 {
		workers.Add(1)
		go func(i int) {
			defer workers.Done()
			fmt.Fprintf(writer, "%012x: Waiting\n", i)
		}(i)
	}
	workers.Wait()
	writer.flush()
	if len(got) != 20 {
		t.Fatalf("lost concurrent layer updates: %d", len(got))
	}
}

func TestPullProgressLimitsRepeatedByteUpdatesButKeepsTransitions(t *testing.T) {
	var got []ImageProgress
	writer := newImageProgressWriter(func(event ImageProgress) { got = append(got, event) })
	for range 20 {
		fmt.Fprintln(writer, "abcdef012345: Downloading [====>] 1MB/2MB")
	}
	fmt.Fprintln(writer, "abcdef012345: Extracting [====>] 1MB/2MB")
	fmt.Fprintln(writer, "abcdef012345: Pull complete")
	if len(got) != 3 || got[0].Status != "Downloading" || got[1].Status != "Extracting" || got[2].Status != "Pull complete" {
		t.Fatalf("progress spam or lost transition: %#v", got)
	}
}

func TestNativeDockerPullFailureNeverIncludesProcessOutput(t *testing.T) {
	docker := pullFixture(t, "printf 'secret-marker\\n'; printf 'secret-marker\\n' >&2; exit 1\n")
	err := docker.Pull(context.Background(), "plugin:1", nil)
	if err == nil || strings.Contains(err.Error(), "secret-marker") {
		t.Fatalf("unsafe failure: %v", err)
	}
}

func TestNativeDockerPullCancellationKillsTheProcess(t *testing.T) {
	// exec avoids leaving a child holding the output pipe open after cancellation.
	docker := pullFixture(t, "exec sleep 60\n")
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	start := time.Now()
	err := docker.Pull(ctx, "plugin:1", nil)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected deadline, got %v", err)
	}
	if time.Since(start) > 3*time.Second {
		t.Fatal("cancelled pull did not stop promptly")
	}
}
