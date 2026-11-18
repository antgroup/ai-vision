/*
 * Alipay.com Inc.
 * Copyright (c) 2004 All Rights Reserved.
 */

package aivisionauthextension

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"testing"
	"time"
)

// signToken is the test-only mirror of what the aivision control plane does:
// base64url(payload).base64url(HMAC-SHA256(secret, base64url(payload))).
func signToken(t *testing.T, secret string, payload tokenPayload) string {
	t.Helper()
	if payload.Version == 0 {
		payload.Version = 2
	}
	if payload.UserID == "" {
		payload.UserID = "subject-test"
	}
	if payload.KeyID == "" {
		payload.KeyID = "key-test"
	}
	if payload.AgentID == "" {
		payload.AgentID = "agent-test"
	}
	if payload.AgentProduct == "" {
		payload.AgentProduct = "Codex"
	}
	if payload.IssuedAt == 0 {
		payload.IssuedAt = time.Now().Unix()
	}
	b, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}
	payloadB64 := base64.RawURLEncoding.EncodeToString(b)
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(payloadB64))
	sig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	return payloadB64 + "." + sig
}

const testSecret = "test-ingest-secret"

func futureExp() int64 { return time.Now().Add(10 * time.Minute).Unix() }

func TestVerifyToken_PythonFixedVector(t *testing.T) {
	const token = "eyJ2IjoyLCJzcCI6InNwYWNlLXZlY3RvciIsInUiOiJzdWJqZWN0LXZlY3RvciIsImtpZCI6ImtleS12ZWN0b3IiLCJhaWQiOiJhZ2VudC12ZWN0b3IiLCJhcCI6IkNvZGV4IiwiaWF0IjoxNzAwMDAwMDAwLCJleHAiOjQxMDI0NDQ4MDAsIm9yZyI6Im9yZy12ZWN0b3IifQ.Y_Yuv6N03Oc1x5kqgsP5d1KX4xFyoi38VsnWbsrvkU4"
	claims, err := VerifyToken("vector-secret", token)
	if err != nil {
		t.Fatalf("fixed Python vector must verify: %v", err)
	}
	if claims.SpaceID != "space-vector" || claims.UserID != "subject-vector" ||
		claims.AgentID != "agent-vector" || claims.AgentProduct != "Codex" {
		t.Fatalf("fixed vector claims not parsed correctly: %+v", claims)
	}
}

func TestVerifyToken_Valid(t *testing.T) {
	tok := signToken(t, testSecret, tokenPayload{
		SpaceID: "a59954e6-e174-43f7-bc10-b1f1cc0e4340",
		OrgID:   "11111111-1111-1111-1111-111111111111",
		UserID:  "100001",
		KeyID:   "sk-aiv-demo",
		Exp:     futureExp(),
	})
	claims, err := VerifyToken(testSecret, "Bearer "+tok)
	if err != nil {
		t.Fatalf("expected valid token, got err: %v", err)
	}
	if claims.SpaceID == "" || claims.UserID != "100001" || claims.KeyID != "sk-aiv-demo" {
		t.Fatalf("claims not parsed correctly: %+v", claims)
	}
}

func TestVerifyToken_Tampered(t *testing.T) {
	tok := signToken(t, testSecret, tokenPayload{
		SpaceID: "space-a", OrgID: "org-a", UserID: "u", Exp: futureExp(),
	})
	// Flip the space id to another tenant while keeping the old signature.
	b, _ := json.Marshal(tokenPayload{SpaceID: "space-b", OrgID: "org-a", UserID: "u", Exp: futureExp()})
	forged := base64.RawURLEncoding.EncodeToString(b) + "." + tok[len(tok)-base64.RawURLEncoding.EncodedLen(32):]
	if _, err := VerifyToken(testSecret, forged); err == nil {
		t.Fatal("expected forged payload to be rejected")
	}
	// A token signed with a different secret must be rejected too.
	wrongSecret := signToken(t, "another-secret", tokenPayload{SpaceID: "space-a", Exp: futureExp()})
	if _, err := VerifyToken(testSecret, wrongSecret); err == nil {
		t.Fatal("expected token signed with wrong secret to be rejected")
	}
}

func TestVerifyToken_Expired(t *testing.T) {
	tok := signToken(t, testSecret, tokenPayload{
		SpaceID: "space-a", OrgID: "org-a", Exp: time.Now().Add(-time.Minute).Unix(),
	})
	if _, err := VerifyToken(testSecret, tok); err == nil {
		t.Fatal("expected expired token to be rejected")
	}
}

func TestVerifyToken_MissingClaims(t *testing.T) {
	// No space id claim.
	if _, err := VerifyToken(testSecret, signToken(t, testSecret, tokenPayload{Exp: futureExp()})); err == nil {
		t.Fatal("expected token without space id to be rejected")
	}
	// No exp claim.
	if _, err := VerifyToken(testSecret, signToken(t, testSecret, tokenPayload{SpaceID: "space-a"})); err == nil {
		t.Fatal("expected token without exp to be rejected")
	}
}

func TestVerifyToken_EmptyAndMalformed(t *testing.T) {
	if _, err := VerifyToken(testSecret, ""); err == nil {
		t.Fatal("expected empty token to be rejected")
	}
	if _, err := VerifyToken(testSecret, "Bearer "); err == nil {
		t.Fatal("expected bearer-only header to be rejected")
	}
	if _, err := VerifyToken(testSecret, "no-dot-here"); err == nil {
		t.Fatal("expected malformed token to be rejected")
	}
}
