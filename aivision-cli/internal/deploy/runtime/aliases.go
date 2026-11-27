package runtime

// This file bridges the runtime engine to the leaf packages it was split out
// of (blueprint, runner, state, proc). The aliases keep the engine code reading
// exactly as it did when everything lived in one package, while the actual
// implementations stay in their own importable packages.

import (
	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"github.com/antgroup/ai-vision/aivision-cli/internal/proc"
	"github.com/antgroup/ai-vision/aivision-cli/internal/runner"
	"github.com/antgroup/ai-vision/aivision-cli/internal/state"
)

// Type aliases re-export the schema and infrastructure types used throughout
// the engine.
type (
	Blueprint = blueprint.Blueprint
	Metadata  = blueprint.Metadata
	Config    = blueprint.Config
	Component = blueprint.Component
	Repo      = blueprint.Repo
	Step      = blueprint.Step
	Discovery = blueprint.Discovery
	Health    = blueprint.Health
	ToolReq   = blueprint.ToolReq

	Logger      = runner.Logger
	RunOpts     = runner.RunOpts
	Docker      = runner.Docker
	State       = state.State
	ManagedRepo = state.ManagedRepo
	DeployMeta  = state.DeployMeta
)

// Function aliases keep the engine's call sites unchanged.
var (
	expand      = blueprint.Expand
	expandStep  = blueprint.ExpandStep
	expandSlice = blueprint.ExpandSlice
	cleanPath   = blueprint.CleanPath

	expandDiscovery = blueprint.ExpandDiscovery

	firstLine = runner.FirstLine
	parseDur  = runner.ParseDur
	NewLogger = runner.NewLogger
	NewDocker = runner.NewDocker
	LookPath  = runner.LookPath

	NewState = state.NewState
	PidAlive = state.PidAlive

	terminateTree = proc.TerminateTree
)
