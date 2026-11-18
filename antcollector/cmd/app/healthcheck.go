package main

import (
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"
)

func runHealthcheck(args []string) error {
	const live = "http://127.0.0.1:13133/debug/pprof/"
	switch {
	case len(args) == 0:
		return healthcheck(live, 2*time.Second)
	case len(args) == 1 && args[0] == "--ready":
		return readiness(live, "http://127.0.0.1:4318", os.Getenv("SIGNOZ_INGEST_ENDPOINT"), 2*time.Second)
	default:
		return fmt.Errorf("healthcheck: invalid arguments; usage: collector healthcheck [--ready]")
	}
}

func healthcheck(endpoint string, timeout time.Duration) error {
	return expectHealthStatus(http.MethodGet, endpoint, http.StatusOK, timeout)
}

// Readiness checks the receiver's authentication boundary without sending data.
// Downstream availability never affects the default local liveness check.
func readiness(live, receiver, downstream string, timeout time.Duration) error {
	if err := healthcheck(live, timeout); err != nil {
		return err
	}
	if err := expectHealthStatus(http.MethodPost, strings.TrimRight(receiver, "/")+"/v1/traces", http.StatusUnauthorized, timeout); err != nil {
		return fmt.Errorf("OTLP authentication readiness: %w", err)
	}
	if downstream == "" {
		return fmt.Errorf("SIGNOZ_INGEST_ENDPOINT is required for readiness")
	}
	return healthcheck(strings.TrimRight(downstream, "/")+"/api/v1/ready", timeout)
}

func expectHealthStatus(method, endpoint string, status int, timeout time.Duration) error {
	client := &http.Client{
		Timeout: timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	request, err := http.NewRequest(method, endpoint, nil)
	if err != nil {
		return fmt.Errorf("healthcheck: %w", err)
	}
	response, err := client.Do(request)
	if err != nil {
		return fmt.Errorf("healthcheck: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != status {
		return fmt.Errorf("healthcheck: unexpected HTTP status %s", response.Status)
	}
	return nil
}
