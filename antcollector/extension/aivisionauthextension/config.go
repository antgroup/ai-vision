/*
 * Alipay.com Inc.
 * Copyright (c) 2004 All Rights Reserved.
 */

package aivisionauthextension

import (
	"errors"

	"go.opentelemetry.io/collector/component"
)

// Config for the aivision HMAC ingest-token authenticator.
type Config struct {
	// Secret is the shared HMAC-SHA256 key also used by the aivision control
	// plane to sign ingest tokens. Usually injected via ${env:AIVISION_INGEST_SECRET}.
	Secret string `mapstructure:"secret"`
	// Header is the request header/metadata key that carries the bearer token.
	// Defaults to "authorization" and is matched case-insensitively.
	Header string `mapstructure:"header"`
}

var _ component.Config = (*Config)(nil)

func (cfg *Config) Validate() error {
	if cfg.Secret == "" {
		return errors.New("aivisionauth: secret must not be empty")
	}
	return nil
}
