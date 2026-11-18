// Command aiv is the unified AI Vision CLI: Foundry-style deployment
// (Compose/Kubernetes/systemd material generation plus a local runtime),
// LangSmith-style data query over the SigNoz API, and automated token-flow auth.
package main

import (
	"github.com/antgroup/ai-vision/aivision-cli/internal/cli"
	"github.com/antgroup/ai-vision/aivision-cli/internal/pathenv"
)

func main() {
	// Make tool resolution robust when launched from a minimal context.
	pathenv.Augment()
	cli.Run(defaultBlueprintYAML)
}
