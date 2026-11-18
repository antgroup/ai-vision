package main

import (
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestHealthcheckHTTPStatus(t *testing.T) {
	for _, status := range []int{http.StatusOK, http.StatusNoContent, http.StatusServiceUnavailable, http.StatusFound} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/redirected" {
					w.WriteHeader(http.StatusOK)
					return
				}
				w.Header().Set("Location", "/redirected")
				w.WriteHeader(status)
			}))
			defer server.Close()
			err := healthcheck(server.URL, time.Second)
			if status == http.StatusOK && err != nil {
				t.Fatalf("healthy endpoint rejected: %v", err)
			}
			if status != http.StatusOK && err == nil {
				t.Fatalf("unhealthy HTTP status %d accepted", status)
			}
		})
	}
}

func TestHealthcheckConnectionRefused(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	endpoint := "http://" + listener.Addr().String()
	listener.Close()
	if err := healthcheck(endpoint, time.Second); err == nil {
		t.Fatal("closed listener accepted as healthy")
	}
}

func TestHealthcheckTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}))
	defer server.Close()
	started := time.Now()
	if err := healthcheck(server.URL, 50*time.Millisecond); err == nil {
		t.Fatal("unresponsive endpoint accepted as healthy")
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("probe exceeded its timeout budget: %s", elapsed)
	}
}

func TestHealthcheckRejectsArguments(t *testing.T) {
	for _, args := range [][]string{{"unexpected"}, {"--config", "unexpected.yaml"}, {"--ready", "unexpected"}, {"--ready", "--ready"}} {
		if err := runHealthcheck(args); err == nil || !strings.Contains(err.Error(), "arguments") {
			t.Fatalf("unexpected healthcheck arguments %q accepted: %v", args, err)
		}
	}
}

func TestReadinessRequiresAuthReceiverAndDownstream(t *testing.T) {
	for _, status := range []int{http.StatusUnauthorized, http.StatusOK, http.StatusServiceUnavailable, http.StatusFound} {
		for _, downstreamStatus := range []int{http.StatusOK, http.StatusServiceUnavailable, http.StatusFound} {
			t.Run(http.StatusText(status)+"/"+http.StatusText(downstreamStatus), func(t *testing.T) {
				server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					switch r.URL.Path {
					case "/debug/pprof/":
						w.WriteHeader(http.StatusOK)
					case "/v1/traces":
						body, err := io.ReadAll(r.Body)
						if r.Method != http.MethodPost || r.Header.Get("Authorization") != "" || err != nil || len(body) != 0 {
							t.Error("readiness must send an empty unauthenticated POST")
						}
						w.Header().Set("Location", "/debug/pprof/")
						w.WriteHeader(status)
					case "/api/v1/ready":
						if r.Method != http.MethodGet {
							t.Error("downstream readiness must be read-only")
						}
						w.Header().Set("Location", "/debug/pprof/")
						w.WriteHeader(downstreamStatus)
					default:
						w.WriteHeader(http.StatusNotFound)
					}
				}))
				defer server.Close()
				err := readiness(server.URL+"/debug/pprof/", server.URL, server.URL+"/", time.Second)
				want := status == http.StatusUnauthorized && downstreamStatus == http.StatusOK
				if (err == nil) != want {
					t.Fatalf("readiness = %v; expected success %v", err, want)
				}
				if err := healthcheck(server.URL+"/debug/pprof/", time.Second); err != nil {
					t.Fatalf("receiver/downstream failure must not affect liveness: %v", err)
				}
			})
		}
	}
}

func TestReadinessRequiresConfiguredDownstream(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/traces" {
			w.WriteHeader(http.StatusUnauthorized)
		}
	}))
	defer server.Close()
	if err := readiness(server.URL, server.URL, "", time.Second); err == nil || !strings.Contains(err.Error(), "SIGNOZ_INGEST_ENDPOINT") {
		t.Fatalf("missing downstream endpoint must fail readiness: %v", err)
	}
	if err := healthcheck(server.URL, time.Second); err != nil {
		t.Fatalf("missing downstream configuration must not affect liveness: %v", err)
	}
}

func TestReadinessTracksDatabaseAvailabilityWhileLivenessRemainsHealthy(t *testing.T) {
	var readyStatus atomic.Int32
	readyStatus.Store(http.StatusOK)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/debug/pprof/", "/api/v1/health":
			w.WriteHeader(http.StatusOK)
		case "/v1/traces":
			w.WriteHeader(http.StatusUnauthorized)
		case "/api/v1/ready":
			w.WriteHeader(int(readyStatus.Load()))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	for _, status := range []int{http.StatusOK, http.StatusServiceUnavailable, http.StatusNotFound, http.StatusOK} {
		readyStatus.Store(int32(status))
		err := readiness(server.URL+"/debug/pprof/", server.URL, server.URL, time.Second)
		if (err == nil) != (status == http.StatusOK) {
			t.Fatalf("database readiness HTTP %d: got %v", status, err)
		}
		if err := healthcheck(server.URL+"/debug/pprof/", time.Second); err != nil {
			t.Fatalf("database readiness must not affect liveness: %v", err)
		}
	}
}

func TestReadinessTimeoutAtEveryStage(t *testing.T) {
	for _, blocked := range []string{"/debug/pprof/", "/v1/traces", "/api/v1/ready"} {
		t.Run(blocked, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == blocked {
					<-r.Context().Done()
					return
				}
				if r.URL.Path == "/v1/traces" {
					w.WriteHeader(http.StatusUnauthorized)
				}
			}))
			defer server.Close()
			started := time.Now()
			if err := readiness(server.URL+"/debug/pprof/", server.URL, server.URL, 50*time.Millisecond); err == nil {
				t.Fatal("unresponsive readiness stage accepted")
			}
			if elapsed := time.Since(started); elapsed > time.Second {
				t.Fatalf("readiness exceeded its timeout budget: %s", elapsed)
			}
		})
	}
}

type healthRoundTripFunc func(*http.Request) (*http.Response, error)

func (f healthRoundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

func TestHealthcheckCommandModes(t *testing.T) {
	transport := http.DefaultTransport
	t.Cleanup(func() { http.DefaultTransport = transport })
	for _, tc := range []struct {
		name       string
		args       []string
		downstream string
		want       []string
	}{
		{"live", nil, "", []string{"GET http://127.0.0.1:13133/debug/pprof/"}},
		{"ready", []string{"--ready"}, "http://downstream.example", []string{
			"GET http://127.0.0.1:13133/debug/pprof/",
			"POST http://127.0.0.1:4318/v1/traces",
			"GET http://downstream.example/api/v1/ready",
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("SIGNOZ_INGEST_ENDPOINT", tc.downstream)
			var requests []string
			http.DefaultTransport = healthRoundTripFunc(func(request *http.Request) (*http.Response, error) {
				requests = append(requests, request.Method+" "+request.URL.String())
				status := http.StatusOK
				if request.URL.Path == "/v1/traces" {
					status = http.StatusUnauthorized
				}
				return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(""))}, nil
			})
			if err := runHealthcheck(tc.args); err != nil {
				t.Fatalf("command rejected valid mode: %v", err)
			}
			if !reflect.DeepEqual(requests, tc.want) {
				t.Fatalf("mode %s requested %q, want %q", tc.name, requests, tc.want)
			}
		})
	}
}
