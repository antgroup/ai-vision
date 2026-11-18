/*
 * Alipay.com Inc.
 * Copyright (c) 2004 All Rights Reserved.
 */

package spaceauthprocessor

import (
	"errors"

	"go.opentelemetry.io/collector/component"
)

// Config for the spaceauth processor.
type Config struct {
	// RequireClaims, when true (the default), rejects a batch that carries no
	// authenticated ingest identity, so telemetry can never land in storage
	// unattributed to an authoritative space. In the aivision topology the OTLP
	// receiver already rejects unauthenticated writes at the aivisionauth layer,
	// so this rarely fires; it is a second gate for when the processor is reused
	// behind a receiver that does not authenticate.
	RequireClaims bool `mapstructure:"require_claims"`
}

var _ component.Config = (*Config)(nil)

func (cfg *Config) Validate() error {
	return nil
}

// errNoClaims is returned by the processors when RequireClaims is set and the
// request carried no verified identity, so the batch never lands unattributed.
var errNoClaims = errors.New("spaceauth: no authenticated ingest claims on request")
