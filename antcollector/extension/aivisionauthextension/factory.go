/*
 * Alipay.com Inc.
 * Copyright (c) 2004 All Rights Reserved.
 */

package aivisionauthextension

import (
	"context"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/extension"
)

// componentType is the OTLP receiver `auth.authenticator` reference name.
var componentType = component.MustNewType("aivisionauth")

// NewFactory creates a factory for the aivision HMAC ingest-token server
// authenticator.
func NewFactory() extension.Factory {
	return extension.NewFactory(
		componentType,
		createDefaultConfig,
		createExtension,
		component.StabilityLevelAlpha,
	)
}

func createDefaultConfig() component.Config {
	return &Config{
		Header: defaultHeader,
	}
}

func createExtension(_ context.Context, set extension.CreateSettings, cfg component.Config) (extension.Extension, error) {
	return newAivisionAuth(cfg.(*Config), set)
}
