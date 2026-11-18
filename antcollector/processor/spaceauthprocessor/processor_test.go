/*
 * Alipay.com Inc.
 * Copyright (c) 2004 All Rights Reserved.
 */

package spaceauthprocessor

import (
	"context"
	"testing"

	"go.opentelemetry.io/collector/pdata/ptrace"

	"aivision/collector/pkg/ingestidentity"
)

func TestProcessTraces_TrustedOverridesClient(t *testing.T) {
	td := ptrace.NewTraces()
	res := td.ResourceSpans().AppendEmpty().Resource().Attributes()
	// A client trying to steer its own partition.
	res.PutStr("space.id", "evil-space")
	res.PutStr("aivision.space.id", "evil-space-2")
	res.PutStr("user.id", "spoofed-user")
	span := td.ResourceSpans().At(0).ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	span.Attributes().PutStr("space.id", "evil-span")

	ctx := ingestidentity.WithClaims(context.Background(), &ingestidentity.Claims{
		SpaceID: "a59954e6-e174-43f7-bc10-b1f1cc0e4340",
		OrgID:   "org-authoritative",
		UserID:  "100001",
		KeyID:   "sk-aiv-demo",
	})

	p := newSpaceAuthProcessor(&Config{RequireClaims: true})
	out, err := p.processTraces(ctx, td)
	if err != nil {
		t.Fatalf("processTraces: %v", err)
	}

	gotRes := out.ResourceSpans().At(0).Resource().Attributes()
	// Client-supplied space keys must be gone.
	for _, k := range ingestidentity.ClientSpaceIDKeys {
		if _, ok := gotRes.Get(k); ok && k != ingestidentity.TrustedSpaceIDKey {
			t.Fatalf("client key %q should have been stripped from resource", k)
		}
	}
	// Trusted keys must be present with authoritative values.
	if v, ok := gotRes.Get(ingestidentity.TrustedSpaceIDKey); !ok || v.AsString() != "a59954e6-e174-43f7-bc10-b1f1cc0e4340" {
		t.Fatalf("trusted space id not stamped: ok=%v", ok)
	}
	if v, ok := gotRes.Get(ingestidentity.TrustedUserIDKey); !ok || v.AsString() != "100001" {
		t.Fatalf("trusted user id not stamped: ok=%v", ok)
	}
	// Span-level client space key must be scrubbed too.
	if _, ok := out.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes().Get("space.id"); ok {
		t.Fatal("client space.id should have been stripped from span attributes")
	}
}

func TestProcessTraces_NoClaimsRequire(t *testing.T) {
	td := ptrace.NewTraces()
	td.ResourceSpans().AppendEmpty().Resource().Attributes().PutStr("space.id", "x")

	p := newSpaceAuthProcessor(&Config{RequireClaims: true})
	if _, err := p.processTraces(context.Background(), td); err == nil {
		t.Fatal("expected rejection when RequireClaims and no claims present")
	}
}

func TestProcessTraces_NoClaimsPassthrough(t *testing.T) {
	td := ptrace.NewTraces()
	td.ResourceSpans().AppendEmpty().Resource().Attributes().PutStr("space.id", "x")

	p := newSpaceAuthProcessor(&Config{RequireClaims: false})
	if _, err := p.processTraces(context.Background(), td); err != nil {
		t.Fatalf("expected passthrough when RequireClaims=false, got %v", err)
	}
}
