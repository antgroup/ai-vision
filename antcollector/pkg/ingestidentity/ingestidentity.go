/*
 * Alipay.com Inc.
 * Copyright (c) 2004 All Rights Reserved.
 */

// Package ingestidentity carries the authoritative tenant identity that the
// gateway derives from an authenticated, aivision-signed ingest token.
//
// Flow: the aivisionauth extension verifies the bearer token offline (HMAC)
// and stashes the parsed claims on the request context; the spaceauth
// processor reads them and stamps them onto the OTLP resource attributes under
// the trusted "aivision.ingest.*" keys while stripping every client-supplied
// space/user attribute so a client cannot steer its own partition. The
// oceanbase exporter then treats only those trusted, resource-level attributes
// as authoritative.
package ingestidentity

import "context"

// Trusted resource-attribute keys written by the spaceauth processor and read
// by the oceanbase exporter. Clients must never be able to set these; the
// processor deletes any inbound copy before stamping the authenticated value.
const (
	TrustedSpaceIDKey      = "aivision.space.id"
	TrustedOrgIDKey        = "aivision.org.id"
	TrustedUserIDKey       = "aivision.user.id"
	TrustedAgentIDKey      = "aivision.agent.id"
	TrustedAgentProductKey = "aivision.agent.product"
	TrustedKeyIDKey        = "aivision.ingest.key_id"
)

// ClientSpaceIDKeys are the attribute names agents commonly use to self-report
// their space. The processor removes all of them (from both resource and span /
// log attribute maps) to close the spoofing path that left the Codex dashboard
// empty when a stale space.id no longer matched any AI Vision space.
var ClientSpaceIDKeys = []string{
	"space_id", "space.id", "aivision.space.id", "ai.vision.space.id", "ant.space.id",
}

var ClientIdentityKeys = []string{
	"user_id", "user.id", "aivision.user.id",
	"agent_id", "agent.id", "aivision.agent.id",
	"agent_product", "agent.product", "aivision.agent.product",
	"aivision.ingest.key_id",
}

// Claims is the authoritative identity resolved from a verified ingest token.
type Claims struct {
	SpaceID      string
	OrgID        string
	UserID       string
	KeyID        string
	AgentID      string
	AgentProduct string
}

type claimsCtxKey struct{}

// WithClaims returns a context carrying the given claims for downstream
// processors to read.
func WithClaims(ctx context.Context, claims *Claims) context.Context {
	return context.WithValue(ctx, claimsCtxKey{}, claims)
}

// FromContext extracts the authenticated claims placed on the context by the
// aivisionauth extension, or nil when the request was not authenticated.
func FromContext(ctx context.Context) *Claims {
	if claims, ok := ctx.Value(claimsCtxKey{}).(*Claims); ok {
		return claims
	}
	return nil
}
