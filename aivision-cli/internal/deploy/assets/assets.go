//go:build !native_distribution

// Package assets embeds generated deployment metadata. Edit deploy/ at the
// repository root and run deploy/scripts/package_assets.py to refresh the bundle.
package assets

import "embed"

//go:embed components.json bootstrap/*
var Files embed.FS
