package main

import (
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestReadinessStatusAndRedirect(t *testing.T) {
	for _, status := range []int{http.StatusOK, http.StatusServiceUnavailable, http.StatusFound} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/ready" {
					t.Errorf("unexpected path %s", r.URL.Path)
				}
				w.Header().Set("Location", "/ready")
				w.WriteHeader(status)
			}))
			defer s.Close()
			host, port, _ := net.SplitHostPort(s.Listener.Addr().String())
			err := probe(host, port, "/ready", time.Second)
			if (err == nil) != (status == http.StatusOK) {
				t.Fatalf("status %d: %v", status, err)
			}
		})
	}
}

func TestTimeoutAndSanitizedFailure(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(100 * time.Millisecond)
	}))
	defer s.Close()
	host, port, _ := net.SplitHostPort(s.Listener.Addr().String())
	err := probe(host, port, "/private-token-value", 20*time.Millisecond)
	if err == nil || strings.Contains(err.Error(), "private-token-value") {
		t.Fatalf("unexpected timeout result: %v", err)
	}
}

func TestArgumentValidation(t *testing.T) {
	for _, args := range [][]string{{}, {"host", "0", "/ready"}, {"host", "80", "ready"}, {"host", "80", "/ready", "0"}, {"host", "80", "/ready", "301"}} {
		if run(args) == nil {
			t.Fatalf("accepted invalid arguments: %v", args)
		}
	}
}
