package sandboxplugin

import (
	"context"
	"errors"
	"fmt"
	"os/exec"
	"regexp"
	"sync"
	"time"
)

// ImageProgress contains only validated Docker layer identifiers, known statuses,
// and bounded byte quantities. Registry messages and image names are not exposed.
type ImageProgress struct {
	Layer   string
	Status  string
	Current string
	Total   string
}

func (d NativeDocker) Pull(ctx context.Context, image string, progress func(ImageProgress)) error {
	if !imagePattern.MatchString(image) {
		return errors.New("Docker image pull requires an explicit valid image reference")
	}
	writer := newImageProgressWriter(progress)
	cmd := exec.CommandContext(ctx, d.binary(), "pull", image)
	cmd.Stdout, cmd.Stderr = writer, writer
	// A cancelled Docker client must not wait indefinitely for an inherited pipe.
	cmd.WaitDelay = 2 * time.Second
	err := cmd.Run()
	writer.flush()
	if ctx.Err() != nil {
		return fmt.Errorf("Docker image pull interrupted: %w", ctx.Err())
	}
	if err != nil {
		// Registry errors can contain credentials. Never forward process output.
		return errors.New("Docker image pull failed")
	}
	return nil
}

const maxPullProgressFrame = 4096
const maxPullProgressLayers = 256

// Match the complete frame. Unrecognized messages, suffixes and terminal control
// sequences are discarded rather than stripped or passed through.
var pullProgressPattern = regexp.MustCompile(`^([a-f0-9]{12,64}): (Pulling fs layer|Waiting|Downloading|Verifying Checksum|Download complete|Extracting|Pull complete|Already exists)(?: +(?:\[[=> ]{1,64}\] +)?([0-9]{1,12}(?:\.[0-9]{1,3})?(?:B|kB|MB|GB|TB|KiB|MiB|GiB|TiB))/([0-9]{1,12}(?:\.[0-9]{1,3})?(?:B|kB|MB|GB|TB|KiB|MiB|GiB|TiB)))?$`)

type pullLayerUpdate struct {
	status string
	at     time.Time
}

type imageProgressWriter struct {
	mu       sync.Mutex
	frame    []byte
	discard  bool
	progress func(ImageProgress)
	layers   map[string]pullLayerUpdate
}

func newImageProgressWriter(progress func(ImageProgress)) *imageProgressWriter {
	return &imageProgressWriter{progress: progress, layers: make(map[string]pullLayerUpdate)}
}

func (w *imageProgressWriter) Write(p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	for _, char := range p {
		if char == '\n' || char == '\r' {
			w.finishFrame()
			continue
		}
		if w.discard {
			continue
		}
		if char < 32 || char > 126 || len(w.frame) == maxPullProgressFrame {
			w.frame = w.frame[:0]
			w.discard = true
			continue
		}
		w.frame = append(w.frame, char)
	}
	// Invalid or overlong lines never stop draining the child process's pipes.
	return len(p), nil
}

func (w *imageProgressWriter) flush() {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.finishFrame()
}

func (w *imageProgressWriter) finishFrame() {
	defer func() {
		w.frame = w.frame[:0]
		w.discard = false
	}()
	if w.discard || w.progress == nil {
		return
	}
	parts := pullProgressPattern.FindSubmatch(w.frame)
	if parts == nil {
		return
	}
	event := ImageProgress{Layer: string(parts[1]), Status: string(parts[2]), Current: string(parts[3]), Total: string(parts[4])}
	if event.Current != "" && event.Status != "Downloading" && event.Status != "Extracting" {
		return
	}
	previous, seen := w.layers[event.Layer]
	if !seen && len(w.layers) >= maxPullProgressLayers {
		return
	}
	now := time.Now()
	if seen && previous.status == event.Status {
		if (event.Status != "Downloading" && event.Status != "Extracting") || now.Sub(previous.at) < time.Second {
			return
		}
	}
	w.layers[event.Layer] = pullLayerUpdate{status: event.Status, at: now}
	w.progress(event)
}
