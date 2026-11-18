/*
 * Alipay.com Inc.
 * Copyright (c) 2004 All Rights Reserved.
 */

package spaceauthprocessor

import (
	"context"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/processor"
	"go.opentelemetry.io/collector/processor/processorhelper"
)

var processorCapabilities = consumer.Capabilities{MutatesData: true}

// componentType is referenced by pipelines as `spaceauth`.
var componentType = component.MustNewType("spaceauth")

// NewFactory returns a factory for the spaceauth processor. It handles all
// three signals so the same authoritative-identity stamping applies to traces,
// logs and metrics uniformly.
func NewFactory() processor.Factory {
	return processor.NewFactory(
		componentType,
		createDefaultConfig,
		processor.WithTraces(createTracesProcessor, component.StabilityLevelAlpha),
		processor.WithLogs(createLogsProcessor, component.StabilityLevelAlpha),
		processor.WithMetrics(createMetricsProcessor, component.StabilityLevelAlpha),
	)
}

func createDefaultConfig() component.Config {
	return &Config{RequireClaims: true}
}

func createTracesProcessor(
	ctx context.Context,
	set processor.CreateSettings,
	cfg component.Config,
	next consumer.Traces,
) (processor.Traces, error) {
	p := newSpaceAuthProcessor(cfg.(*Config))
	return processorhelper.NewTracesProcessor(ctx, set, cfg, next,
		p.processTraces,
		processorhelper.WithCapabilities(processorCapabilities))
}

func createLogsProcessor(
	ctx context.Context,
	set processor.CreateSettings,
	cfg component.Config,
	next consumer.Logs,
) (processor.Logs, error) {
	p := newSpaceAuthProcessor(cfg.(*Config))
	return processorhelper.NewLogsProcessor(ctx, set, cfg, next,
		p.processLogs,
		processorhelper.WithCapabilities(processorCapabilities))
}

func createMetricsProcessor(
	ctx context.Context,
	set processor.CreateSettings,
	cfg component.Config,
	next consumer.Metrics,
) (processor.Metrics, error) {
	p := newSpaceAuthProcessor(cfg.(*Config))
	return processorhelper.NewMetricsProcessor(ctx, set, cfg, next,
		p.processMetrics,
		processorhelper.WithCapabilities(processorCapabilities))
}
