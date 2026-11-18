/*
 * Alipay.com Inc.
 * Copyright (c) 2004 All Rights Reserved.
 */

package aivisionauthextension

import (
	"os"
	"testing"
)

// TestVerifyToken_InteropWithPythonSigner proves the Go verifier accepts tokens
// signed by the aivision control plane's Python service
// (apps/core/services/ingest_token.py) — i.e. the two implementations are
// byte-compatible across the shared HMAC secret and base64url wire format.
//
// It is skipped unless the fixtures are supplied via env (see scripts for how
// they are generated), so a committed suite never breaks on an expiring token.
//
//	AIVISION_INTEROP_SECRET           shared HMAC key
//	AIVISION_INTEROP_VALID_TOKEN      a not-yet-expired token from the Python signer
//	AIVISION_INTEROP_EXPIRED_TOKEN    an expired token from the Python signer
//	AIVISION_INTEROP_EXPECTED_SPACE   the space uuid the valid token carries
func TestVerifyToken_InteropWithPythonSigner(t *testing.T) {
	secret := os.Getenv("AIVISION_INTEROP_SECRET")
	valid := os.Getenv("AIVISION_INTEROP_VALID_TOKEN")
	if secret == "" || valid == "" {
		t.Skip("interop fixtures not provided via env; skipping cross-language check")
	}

	claims, err := VerifyToken(secret, "Bearer "+valid)
	if err != nil {
		t.Fatalf("Go rejected a Python-signed token (format drift!): %v", err)
	}
	if want := os.Getenv("AIVISION_INTEROP_EXPECTED_SPACE"); want != "" && claims.SpaceID != want {
		t.Fatalf("space mismatch: go=%q want=%q", claims.SpaceID, want)
	}
	t.Logf("interop claims: %+v", claims)

	if expired := os.Getenv("AIVISION_INTEROP_EXPIRED_TOKEN"); expired != "" {
		if _, err := VerifyToken(secret, expired); err == nil {
			t.Fatal("Go accepted an expired Python-signed token")
		}
	}
}
