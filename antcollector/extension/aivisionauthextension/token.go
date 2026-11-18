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
	"errors"
	"fmt"
	"strings"
	"time"

	"aivision/collector/pkg/ingestidentity"
)

// tokenPayload is the signed claim set aivision stamps onto every ingest
// token. It is deliberately compact because it travels in an Authorization
// header on every OTLP push. Wire format:
//
//	base64url(payloadJSON) + "." + base64url(HMAC-SHA256(secret, payloadB64))
//
// The control plane owns the secret and the TTL; the gateway only ever
// verifies offline, so no runtime round-trip back to aivision is required.
type tokenPayload struct {
	Version      int    `json:"v"`
	SpaceID      string `json:"sp"`
	OrgID        string `json:"org"`
	UserID       string `json:"u"`
	KeyID        string `json:"kid"`
	AgentID      string `json:"aid"`
	AgentProduct string `json:"ap"`
	IssuedAt     int64  `json:"iat"`
	Exp          int64  `json:"exp"`
}

// VerifyToken validates an aivision-signed ingest token against the shared
// HMAC secret and returns the authoritative claims it carries. It rejects
// empty, malformed, tampered and expired tokens so that unauthenticated or
// spoofed writes never reach storage.
func VerifyToken(secret, token string) (*ingestidentity.Claims, error) {
	token = strings.TrimSpace(token)
	token = strings.TrimPrefix(token, "Bearer ")
	token = strings.TrimPrefix(token, "bearer ")
	token = strings.TrimSpace(token)
	if token == "" {
		return nil, errors.New("aivisionauth: empty token")
	}
	if secret == "" {
		return nil, errors.New("aivisionauth: secret not configured")
	}

	parts := strings.Split(token, ".")
	if len(parts) != 2 {
		return nil, errors.New("aivisionauth: malformed token, expected payload.signature")
	}
	payloadB64, sigB64 := parts[0], parts[1]

	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(payloadB64))
	expectedSig := mac.Sum(nil)

	providedSig, err := base64.RawURLEncoding.DecodeString(sigB64)
	if err != nil {
		if providedSig, err = base64.URLEncoding.DecodeString(sigB64); err != nil {
			return nil, fmt.Errorf("aivisionauth: cannot decode signature: %w", err)
		}
	}
	if !hmac.Equal(providedSig, expectedSig) {
		return nil, errors.New("aivisionauth: signature mismatch (tampered or wrong secret)")
	}

	payloadBytes, err := base64.RawURLEncoding.DecodeString(payloadB64)
	if err != nil {
		if payloadBytes, err = base64.URLEncoding.DecodeString(payloadB64); err != nil {
			return nil, fmt.Errorf("aivisionauth: cannot decode payload: %w", err)
		}
	}
	var payload tokenPayload
	if err := json.Unmarshal(payloadBytes, &payload); err != nil {
		return nil, fmt.Errorf("aivisionauth: cannot parse claims: %w", err)
	}

	if payload.Version != 2 || payload.SpaceID == "" || payload.UserID == "" ||
		payload.KeyID == "" || payload.AgentID == "" || payload.AgentProduct == "" ||
		payload.IssuedAt == 0 || payload.Exp == 0 {
		return nil, errors.New("aivisionauth: token missing required v2 claims")
	}
	now := time.Now().Unix()
	if payload.IssuedAt > now {
		return nil, errors.New("aivisionauth: token issued in the future")
	}
	if now >= payload.Exp {
		return nil, errors.New("aivisionauth: token expired")
	}

	return &ingestidentity.Claims{
		SpaceID:      payload.SpaceID,
		OrgID:        payload.OrgID,
		UserID:       payload.UserID,
		KeyID:        payload.KeyID,
		AgentID:      payload.AgentID,
		AgentProduct: payload.AgentProduct,
	}, nil
}
