/*
 * Alipay.com Inc.
 * Copyright (c) 2004 All Rights Reserved.
 */

package aivisionauthextension

import (
	"context"
	"testing"

	"go.uber.org/zap"

	"aivision/collector/pkg/ingestidentity"
)

func newTestAuth() *aivisionAuth {
	return &aivisionAuth{
		cfg:            &Config{Secret: testSecret},
		logger:         zap.NewNop(),
		resolvedHeader: defaultHeader,
	}
}

// A compiled agent hook may emit an unrelated Basic credential alongside our
// bearer ingest token under the same header. authenticate must still accept the
// verifiable bearer value rather than being masked by the foreign credential.
func TestAuthenticate_ToleratesForeignAuthorizationValue(t *testing.T) {
	tok := signToken(t, testSecret, tokenPayload{
		SpaceID: "space-a", OrgID: "org-a", UserID: "u-1", Exp: futureExp(),
	})
	a := newTestAuth()
	ctx, err := a.authenticate(context.Background(), map[string][]string{
		"authorization": {"Basic dXNlcjpwYXNz", "Bearer " + tok},
	})
	if err != nil {
		t.Fatalf("expected bearer token to win over Basic, got: %v", err)
	}
	claims := ingestidentity.FromContext(ctx)
	if claims == nil || claims.SpaceID != "space-a" || claims.UserID != "u-1" {
		t.Fatalf("authoritative claims not attached: %+v", claims)
	}
}

// When no candidate value verifies (only a Basic credential, no ingest token),
// the request must still be rejected.
func TestAuthenticate_RejectsWhenNoVerifiableValue(t *testing.T) {
	a := newTestAuth()
	if _, err := a.authenticate(context.Background(), map[string][]string{
		"authorization": {"Basic dXNlcjpwYXNz"},
	}); err == nil {
		t.Fatal("expected request without a valid ingest token to be rejected")
	}
}

func TestAuthenticate_MissingHeader(t *testing.T) {
	a := newTestAuth()
	if _, err := a.authenticate(context.Background(), map[string][]string{}); err == nil {
		t.Fatal("expected missing header to be rejected")
	}
}
