/*
 * Alipay.com Inc.
 * Copyright (c) 2004 All Rights Reserved.
 */

package aivisionauthextension

import (
	"context"
	"errors"
	"strings"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/extension"
	"go.opentelemetry.io/collector/extension/auth"
	"go.uber.org/zap"

	"aivision/collector/pkg/ingestidentity"
)

const defaultHeader = "authorization"

// aivisionAuth is a server authenticator: OTLP receivers reference it via
// their `auth.authenticator` setting, and every inbound push must present a
// valid aivision-signed ingest token or the call is rejected.
//
// It embeds auth.Server (built with auth.NewServer) so the returned extension
// satisfies the interface configauth.GetServerAuthenticator type-asserts to,
// inheriting Start/Shutdown/Authenticate.
type aivisionAuth struct {
	cfg    *Config
	logger *zap.Logger
	auth.Server

	// resolvedHeader is the lower-cased header/metadata key to read.
	resolvedHeader string
}

func newAivisionAuth(cfg *Config, set extension.CreateSettings) (*aivisionAuth, error) {
	if cfg.Secret == "" {
		return nil, errors.New("aivisionauth: secret must not be empty")
	}
	a := &aivisionAuth{
		cfg:            cfg,
		logger:         set.Logger,
		resolvedHeader: defaultHeader,
	}
	if cfg.Header != "" {
		a.resolvedHeader = strings.ToLower(cfg.Header)
	}
	a.Server = auth.NewServer(auth.WithServerAuthenticate(a.authenticate))
	return a, nil
}

func (a *aivisionAuth) Start(ctx context.Context, host component.Host) error {
	a.logger.Info("aivisionauth: ingest-token authenticator active")
	return a.Server.Start(ctx, host)
}

// authenticate pulls the bearer token from the request headers, verifies it
// offline against the shared HMAC secret, and, on success, attaches the
// authoritative claims to the returned context for the spaceauth processor.
func (a *aivisionAuth) authenticate(ctx context.Context, headers map[string][]string) (context.Context, error) {
	values := grabHeaderValues(headers, a.resolvedHeader)
	if len(values) == 0 {
		return ctx, errors.New("aivisionauth: missing ingest token in " + a.resolvedHeader + " header")
	}
	// A client (e.g. a compiled agent hook that also emits an unrelated Basic
	// credential) may carry several values under the same header. Accept the
	// first candidate that actually verifies so a foreign credential can never
	// mask a valid ingest token by happening to sort first.
	var lastErr error
	for _, value := range values {
		claims, err := VerifyToken(a.cfg.Secret, value)
		if err != nil {
			lastErr = err
			continue
		}
		return ingestidentity.WithClaims(ctx, claims), nil
	}
	return ctx, lastErr
}

// grabHeaderValues collects every value stored under the named key, matching
// case-insensitively: HTTP receivers canonicalize to "Authorization" while
// gRPC metadata is already lowercase.
func grabHeaderValues(headers map[string][]string, name string) []string {
	var out []string
	for k, v := range headers {
		if strings.ToLower(k) != name {
			continue
		}
		out = append(out, v...)
	}
	return out
}

var (
	_ extension.Extension = (*aivisionAuth)(nil)
	_ auth.Server         = (*aivisionAuth)(nil)
)
