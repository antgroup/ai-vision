/*
 * Alipay.com Inc.
 * Copyright (c) 2004 All Rights Reserved.
 */

package spaceauthprocessor

import (
	"context"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/ptrace"

	"aivision/collector/pkg/ingestidentity"
)

// spaceauthProcessor stamps the authoritative tenant identity derived from the
// verified ingest token onto every OTLP resource and strips every client-
// supplied space/user attribute, so the oceanbase exporter can trust the
// partition it writes to regardless of what the agent claimed.
type spaceauthProcessor struct {
	cfg *Config
}

func newSpaceAuthProcessor(cfg *Config) *spaceauthProcessor {
	return &spaceauthProcessor{cfg: cfg}
}

// claims resolves the authenticated identity for the current request, or nil.
func (p *spaceauthProcessor) claims(ctx context.Context) *ingestidentity.Claims {
	return ingestidentity.FromContext(ctx)
}

func (p *spaceauthProcessor) processTraces(ctx context.Context, td ptrace.Traces) (ptrace.Traces, error) {
	if err := p.applyAll(ctx, td.ResourceSpans().Len(), func(i int) pcommon.Map {
		return td.ResourceSpans().At(i).Resource().Attributes()
	}); err != nil {
		return td, err
	}
	// Also scrub spoofable space/user keys from span-level attributes so the
	// exporter's signal-level lookup can never surface a client value.
	for i := 0; i < td.ResourceSpans().Len(); i++ {
		rss := td.ResourceSpans().At(i).ScopeSpans()
		for j := 0; j < rss.Len(); j++ {
			spans := rss.At(j).Spans()
			for k := 0; k < spans.Len(); k++ {
				stripClientKeys(spans.At(k).Attributes())
			}
		}
	}
	return td, nil
}

func (p *spaceauthProcessor) processLogs(ctx context.Context, ld plog.Logs) (plog.Logs, error) {
	if err := p.applyAll(ctx, ld.ResourceLogs().Len(), func(i int) pcommon.Map {
		return ld.ResourceLogs().At(i).Resource().Attributes()
	}); err != nil {
		return ld, err
	}
	for i := 0; i < ld.ResourceLogs().Len(); i++ {
		rls := ld.ResourceLogs().At(i).ScopeLogs()
		for j := 0; j < rls.Len(); j++ {
			logs := rls.At(j).LogRecords()
			for k := 0; k < logs.Len(); k++ {
				stripClientKeys(logs.At(k).Attributes())
			}
		}
	}
	return ld, nil
}

func (p *spaceauthProcessor) processMetrics(ctx context.Context, md pmetric.Metrics) (pmetric.Metrics, error) {
	// Metrics carry no per-point space attribute in the OTLP profiles this
	// gateway ingests; the authoritative space lives on the resource, which
	// applyAll strips and stamps below.
	if err := p.applyAll(ctx, md.ResourceMetrics().Len(), func(i int) pcommon.Map {
		return md.ResourceMetrics().At(i).Resource().Attributes()
	}); err != nil {
		return md, err
	}
	return md, nil
}

// applyAll strips client keys and stamps trusted keys onto each resource
// attribute map. It enforces the RequireClaims gate once for the whole batch.
func (p *spaceauthProcessor) applyAll(ctx context.Context, n int, attrsAt func(int) pcommon.Map) error {
	claims := p.claims(ctx)
	if claims == nil {
		if p.cfg.RequireClaims {
			return errNoClaims
		}
		return nil
	}
	for i := 0; i < n; i++ {
		attrs := attrsAt(i)
		stripClientKeys(attrs)
		stampTrusted(attrs, claims)
	}
	return nil
}

// stripClientKeys removes every attribute name an agent could use to self-report
// its space, closing the path that left the dashboard empty on a stale space.id.
func stripClientKeys(attrs pcommon.Map) {
	for _, key := range ingestidentity.ClientSpaceIDKeys {
		attrs.Remove(key)
	}
	for _, key := range ingestidentity.ClientIdentityKeys {
		attrs.Remove(key)
	}
}

// stampTrusted writes the authenticated identity under the reserved trusted keys
// the oceanbase exporter treats as authoritative.
func stampTrusted(attrs pcommon.Map, claims *ingestidentity.Claims) {
	if claims.SpaceID != "" {
		attrs.PutStr(ingestidentity.TrustedSpaceIDKey, claims.SpaceID)
	}
	if claims.OrgID != "" {
		attrs.PutStr(ingestidentity.TrustedOrgIDKey, claims.OrgID)
	}
	if claims.UserID != "" {
		attrs.PutStr(ingestidentity.TrustedUserIDKey, claims.UserID)
	}
	if claims.KeyID != "" {
		attrs.PutStr(ingestidentity.TrustedKeyIDKey, claims.KeyID)
	}
	if claims.AgentID != "" {
		attrs.PutStr(ingestidentity.TrustedAgentIDKey, claims.AgentID)
	}
	if claims.AgentProduct != "" {
		attrs.PutStr(ingestidentity.TrustedAgentProductKey, claims.AgentProduct)
	}
}
