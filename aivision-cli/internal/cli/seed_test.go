package cli

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestTraceIngestURL(t *testing.T) {
	tests := []struct {
		name     string
		endpoint string
		want     string
		wantErr  bool
	}{
		{name: "gateway base", endpoint: "http://gateway:4318", want: "http://gateway:4318/v1/traces"},
		{name: "gateway base with path", endpoint: "http://gateway:4318/otel", want: "http://gateway:4318/otel/v1/traces"},
		{name: "gateway base under v1", endpoint: "http://gateway:4318/v1/otel", want: "http://gateway:4318/v1/otel/v1/traces"},
		{name: "trace endpoint", endpoint: "http://gateway:4318/v1/traces", want: "http://gateway:4318/v1/traces"},
		{name: "nested trace endpoint", endpoint: "http://gateway:4318/otel/v1/traces/", want: "http://gateway:4318/otel/v1/traces"},
		{name: "other signal endpoint", endpoint: "http://gateway:4318/v1/logs", wantErr: true},
		{name: "nested metrics endpoint", endpoint: "http://gateway:4318/otel/v1/metrics", wantErr: true},
		{name: "invalid URL", endpoint: "gateway:4318", wantErr: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := traceIngestURL(tt.endpoint)
			if tt.wantErr {
				if err == nil {
					t.Fatalf("traceIngestURL(%q) = %q, want an error", tt.endpoint, got)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if got != tt.want {
				t.Fatalf("traceIngestURL(%q) = %q, want %q", tt.endpoint, got, tt.want)
			}
		})
	}
}

func TestPushTracesPreservesCompleteMintedEndpoint(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/traces" {
			t.Fatalf("push path = %q", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer token" {
			t.Fatalf("authorization = %q", r.Header.Get("Authorization"))
		}
		if _, err := io.ReadAll(r.Body); err != nil {
			t.Fatal(err)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	if err := pushTraces(context.Background(), server.URL+"/v1/traces", "token", []byte(`{}`)); err != nil {
		t.Fatal(err)
	}
}
