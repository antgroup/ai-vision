package sandboxplugin

import (
	"context"
	"errors"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

type observedDocker struct {
	*fakeDocker
	imageMissing bool
	pullErr      error
	pullDelay    time.Duration
	beforeRun    func([]string)
}

func (d *observedDocker) Run(ctx context.Context, args ...string) ([]byte, error) {
	if d.beforeRun != nil {
		d.beforeRun(args)
	}
	if args[0] == "image" && d.imageMissing {
		return nil, errors.New("missing")
	}
	return d.fakeDocker.Run(ctx, args...)
}

func (d *observedDocker) Pull(ctx context.Context, image string, notify func(ImageProgress)) error {
	if notify != nil {
		notify(ImageProgress{Layer: "0123456789ab", Status: "Downloading", Current: "1MB", Total: "2MB"})
	}
	if d.pullDelay > 0 {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(d.pullDelay):
		}
	}
	return d.pullErr
}

func TestInstallProgressReportsCacheAndEveryCompletedPhase(t *testing.T) {
	f := setup(t)
	var events []ProgressEvent
	f.m.Progress = func(e ProgressEvent) { events = append(events, e) }
	f.m.Docker = &observedDocker{fakeDocker: f.docker, beforeRun: func(args []string) {
		if len(events) == 0 || events[0].Stage != "configuration" {
			t.Error("install did not report configuration before Docker work")
		}
	}}
	f.install(t)
	var completed []string
	for _, event := range events {
		if event.State == "done" {
			completed = append(completed, event.Stage+":"+event.Detail)
		}
		if event.Elapsed < 0 {
			t.Fatal("negative elapsed time")
		}
	}
	want := "configuration:,docker:,image:cached,state:,container:,readiness:ready"
	if strings.Join(completed, ",") != want {
		t.Fatalf("completed phases = %q, want %q", strings.Join(completed, ","), want)
	}
}

func TestInstallProgressReportsPullFailureWithoutStartingContainer(t *testing.T) {
	f := setup(t)
	f.m.Docker = &observedDocker{fakeDocker: f.docker, imageMissing: true, pullErr: errors.New("unsafe-Docker-token")}
	var events []ProgressEvent
	f.m.Progress = func(e ProgressEvent) { events = append(events, e) }
	_, err := f.m.Install(context.Background(), f.options)
	if err == nil || strings.Contains(err.Error(), "unsafe-Docker-token") {
		t.Fatalf("pull failure was not safely reported: %v", err)
	}
	if f.docker.created {
		t.Fatal("container started after pull failure")
	}
	var sawPull, sawLayer bool
	for _, event := range events {
		sawPull = sawPull || event.Stage == "image" && event.Detail == "pulling"
		sawLayer = sawLayer || event.Image != nil && event.Image.Layer == "0123456789ab"
	}
	if len(events) == 0 || events[len(events)-1].Stage != "image" || events[len(events)-1].State != "failed" || !sawPull || !sawLayer {
		t.Fatalf("missing pull progress/failure: %#v", events)
	}
	if _, err := os.Stat(f.m.statePath()); !os.IsNotExist(err) {
		t.Fatalf("pull failure should not persist an installation: %v", err)
	}
}

func TestInstallProgressDistinguishesNotReadyFromInstallationFailure(t *testing.T) {
	f := setup(t)
	f.draining = true
	var events []ProgressEvent
	f.m.Progress = func(e ProgressEvent) { events = append(events, e) }
	status, err := f.m.Install(context.Background(), f.options)
	if err != nil || status == nil || status.Health == nil || *status.Health.Ready {
		t.Fatalf("installed/not-ready contract changed: %#v, %v", status, err)
	}
	if len(events) == 0 || events[len(events)-1].State != "done" || events[len(events)-1].Detail != "not_ready" {
		t.Fatalf("provider was reported ready: %#v", events)
	}
}

func TestInstallProgressCancellationStopsObserverAndRetainsSafeFailure(t *testing.T) {
	f := setup(t)
	f.m.Docker = &observedDocker{fakeDocker: f.docker, imageMissing: true, pullDelay: time.Minute}
	ctx, cancel := context.WithCancel(context.Background())
	var events []ProgressEvent
	f.m.Progress = func(e ProgressEvent) {
		events = append(events, e)
		if e.Image != nil {
			cancel()
		}
	}
	_, err := f.m.Install(ctx, f.options)
	if err == nil || !strings.Contains(err.Error(), "cancel") || !strings.Contains(err.Error(), "image") {
		t.Fatalf("missing stage/cancellation guidance: %v", err)
	}
	last := events[len(events)-1]
	if last.Stage != "image" || last.State != "failed" || last.Detail != "canceled" {
		t.Fatalf("last event = %#v", last)
	}
}

func TestInstallProgressHeartbeatArrivesBeforeSlowPullCompletes(t *testing.T) {
	f := setup(t)
	f.m.Docker = &observedDocker{fakeDocker: f.docker, imageMissing: true, pullDelay: 5500 * time.Millisecond}
	var mu sync.Mutex
	var events []ProgressEvent
	f.m.Progress = func(e ProgressEvent) { mu.Lock(); defer mu.Unlock(); events = append(events, e) }
	f.install(t)
	mu.Lock()
	defer mu.Unlock()
	var heartbeat bool
	for _, event := range events {
		if event.Stage == "image" && event.State == "wait" && event.Image == nil && event.Elapsed >= 5*time.Second {
			heartbeat = true
		}
	}
	if !heartbeat {
		t.Fatalf("slow image pull had no heartbeat: %#v", events)
	}
}
