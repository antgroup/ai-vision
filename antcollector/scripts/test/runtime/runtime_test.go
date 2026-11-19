package runtime_test

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"
	"time"

	"aivision/collector/pkg/ingestidentity"
	_ "go.opentelemetry.io/collector/config/configgrpc" // Register the production compression codecs.
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog/plogotlp"
	"go.opentelemetry.io/collector/pdata/pmetric/pmetricotlp"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// Requires Linux and a local Docker engine (the stub binds the bridge gateway).
// Run against a built image without contacting a deployed backend or SigNoz:
//
//	AIVISION_COLLECTOR_IMAGE=local/antcollector-gw:slim-runtime \
//	  go test -mod=vendor -v ./scripts/test/runtime
//
// Use a native Linux host for each image architecture. The test's temporary
// directory must be visible at the same path to the Docker engine when this
// test itself runs in a helper container (mount it and set TMPDIR explicitly).
func TestImageGateway(t *testing.T) {
	image := os.Getenv("AIVISION_COLLECTOR_IMAGE")
	if image == "" {
		t.Skip("set AIVISION_COLLECTOR_IMAGE to run the Docker runtime smoke")
	}
	if runtime.GOOS != "linux" {
		t.Skip("runtime smoke requires a Linux host with a local Docker engine")
	}
	if arch := docker(t, "image", "inspect", "--format", "{{.Architecture}}", image); arch != runtime.GOARCH {
		t.Fatalf("native validation requires image architecture %s, got %s", runtime.GOARCH, arch)
	}
	t.Logf("native Linux %s image %s", runtime.GOARCH, image)
	name := fmt.Sprintf("aiv-scratch-collector-%d", time.Now().UnixNano())
	docker(t, "network", "create", name)
	t.Cleanup(func() { exec.Command("docker", "network", "rm", name).Run() })
	gateway := docker(t, "network", "inspect", "--format", "{{(index .IPAM.Config 0).Gateway}}", name)
	listener, err := net.Listen("tcp", net.JoinHostPort(gateway, "0"))
	if err != nil {
		t.Fatal(err)
	}
	events := make(chan string, 12)
	sink := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Signoz-Ingest-Key") != "runtime-smoke-internal" || r.Header.Get("Content-Encoding") != "" {
			t.Error("exporter lost its internal key or enabled compression")
		}
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
		}
		check := func(attrs pcommon.Map) {
			for key, want := range trustedIdentity {
				got, ok := attrs.Get(key)
				if !ok {
					t.Errorf("%s: missing %s", r.URL.Path, key)
					continue
				}
				if got.AsString() != want {
					t.Errorf("%s: %s = %q, want %q", r.URL.Path, key, got.AsString(), want)
				}
			}
			if _, ok := attrs.Get("space.id"); ok {
				t.Error("client space identity survived")
			}
			marker, ok := attrs.Get("smoke.case")
			if !ok {
				t.Error("exporter lost the request marker")
				return
			}
			select {
			case events <- r.URL.Path + ":" + marker.AsString():
			default:
				t.Error("unexpected excess forwarded resources")
			}
		}
		switch r.URL.Path {
		case "/v1/traces":
			req := ptraceotlp.NewExportRequest()
			if err := req.UnmarshalProto(body); err != nil {
				t.Error(err)
			}
			resources := req.Traces().ResourceSpans()
			for i := 0; i < resources.Len(); i++ {
				check(resources.At(i).Resource().Attributes())
			}
		case "/v1/logs":
			req := plogotlp.NewExportRequest()
			if err := req.UnmarshalProto(body); err != nil {
				t.Error(err)
			}
			resources := req.Logs().ResourceLogs()
			for i := 0; i < resources.Len(); i++ {
				check(resources.At(i).Resource().Attributes())
			}
		case "/v1/metrics":
			req := pmetricotlp.NewExportRequest()
			if err := req.UnmarshalProto(body); err != nil {
				t.Error(err)
			}
			resources := req.Metrics().ResourceMetrics()
			for i := 0; i < resources.Len(); i++ {
				check(resources.At(i).Resource().Attributes())
			}
		default:
			t.Errorf("unexpected export path %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/x-protobuf")
		w.WriteHeader(http.StatusOK)
	}))
	sink.Listener.Close()
	sink.Listener = listener
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	certificate := &x509.Certificate{
		SerialNumber: big.NewInt(1), DNSNames: []string{"aiv-slim-sink"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true, IsCA: true,
	}
	der, err := x509.CreateCertificate(rand.Reader, certificate, certificate, publicKey, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	caFile := filepath.Join(t.TempDir(), "sink-ca.pem")
	if err := os.WriteFile(caFile, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0644); err != nil {
		t.Fatal(err)
	}
	sink.TLS = &tls.Config{Certificates: []tls.Certificate{{Certificate: [][]byte{der}, PrivateKey: privateKey}}}
	sink.StartTLS()
	t.Cleanup(sink.Close)
	_, port, _ := net.SplitHostPort(listener.Addr().String())
	args := []string{"run", "-d", "--name", name, "--network", name, "--read-only",
		"--add-host", "aiv-slim-sink:" + gateway,
		"--mount", "type=bind,src=" + caFile + ",dst=/etc/ssl/aiv-slim-ca.pem,readonly",
		// SSL_CERT_DIR stays unchanged, retaining the image's system CA store.
		"-e", "SSL_CERT_FILE=/etc/ssl/aiv-slim-ca.pem",
		"-e", "TZ=Asia/Shanghai",
		"-p", "127.0.0.1::4317", "-p", "127.0.0.1::4318", "-p", "127.0.0.1::13133",
		"-e", "AIVISION_INGEST_SECRET=runtime-smoke-secret",
		"-e", "SIGNOZ_INGEST_INTERNAL_KEY=runtime-smoke-internal",
		"-e", "SIGNOZ_INGEST_ENDPOINT=https://aiv-slim-sink:" + port}
	if platform := os.Getenv("AIVISION_COLLECTOR_PLATFORM"); platform != "" {
		args = append(args, "--platform", platform)
	}
	t.Cleanup(func() {
		if t.Failed() {
			logs, _ := exec.Command("docker", "logs", name).CombinedOutput()
			t.Log(string(logs))
		}
		exec.Command("docker", "rm", "-f", name).Run()
	})
	docker(t, append(args, image)...)
	healthURL := "http://" + docker(t, "port", name, "13133/tcp") + "/debug/pprof/"
	client := &http.Client{Timeout: 5 * time.Second}
	deadline := time.Now().Add(30 * time.Second)
	for {
		response, err := client.Get(healthURL)
		if err == nil {
			response.Body.Close()
			if response.StatusCode == http.StatusOK {
				break
			}
		}
		if time.Now().After(deadline) {
			t.Fatal("gateway did not start within 30 seconds")
		}
		time.Sleep(100 * time.Millisecond)
	}
	docker(t, "exec", name, "/app/collector", "healthcheck")
	assertScratchRuntime(t, name)
	for _, args := range [][]string{
		{"exec", name, "/app/collector", "healthcheck", "unexpected"},
		{"run", "--rm", "--network", "none", "--read-only", image, "healthcheck"},
	} {
		probeCtx, probeCancel := context.WithTimeout(context.Background(), 5*time.Second)
		out, err := exec.CommandContext(probeCtx, "docker", args...).CombinedOutput()
		probeCancel()
		if err == nil || !bytes.Contains(out, []byte("healthcheck")) {
			t.Fatalf("invalid or disconnected probe did not fail: %v\n%s", err, out)
		}
	}
	httpURL := "http://" + docker(t, "port", name, "4318/tcp")
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	conn, err := grpc.DialContext(ctx, docker(t, "port", name, "4317/tcp"), grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithBlock())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	validToken := signToken(time.Now().Add(time.Minute).Unix())
	signatureStart := strings.LastIndex(validToken, ".") + 1
	replacement := "A"
	if validToken[signatureStart] == 'A' {
		replacement = "B"
	}
	badSignature := validToken[:signatureStart] + replacement + validToken[signatureStart+1:]
	invalidTokens := []string{"", "Bearer invalid", badSignature, signToken(time.Now().Add(-time.Minute).Unix())}
	protocols := []string{"http", "grpc", "grpc-zstd"}
	for _, signal := range []string{"traces", "logs", "metrics"} {
		for _, protocol := range protocols {
			t.Run(protocol+"/"+signal, func(t *testing.T) {
				body, export := request(signal, protocol, conn)
				for _, token := range append(invalidTokens, validToken) {
					if protocol == "http" {
						req, _ := http.NewRequestWithContext(ctx, http.MethodPost, httpURL+"/v1/"+signal, bytes.NewReader(body))
						req.Header.Set("Content-Type", "application/x-protobuf")
						req.Header.Set("Authorization", token)
						response, err := client.Do(req)
						if err != nil {
							t.Fatal(err)
						}
						response.Body.Close()
						if token == validToken && response.StatusCode != http.StatusOK || token != validToken && response.StatusCode != http.StatusUnauthorized {
							t.Fatalf("unexpected HTTP auth status %d", response.StatusCode)
						}
					} else {
						err := export(metadata.AppendToOutgoingContext(ctx, "authorization", token))
						// The existing authenticator returns plain Go errors, which
						// gRPC exposes as Unknown rather than Unauthenticated.
						rejected := status.Code(err) == codes.Unauthenticated || status.Code(err) == codes.Unknown && strings.Contains(err.Error(), "aivisionauth:")
						if token == validToken && err != nil || token != validToken && !rejected {
							t.Fatalf("unexpected gRPC auth result: %v", err)
						}
					}
				}
			})
		}
	}
	want := map[string]bool{}
	for _, signal := range []string{"traces", "logs", "metrics"} {
		for _, protocol := range protocols {
			want["/v1/"+signal+":"+protocol] = true
		}
	}
	for len(want) > 0 {
		select {
		case event := <-events:
			if !want[event] {
				t.Fatalf("unexpected or duplicate forwarded resource: %s", event)
			}
			delete(want, event)
		case <-ctx.Done():
			t.Fatalf("missing forwarded resources: %v", want)
		}
	}
	select {
	case event := <-events:
		t.Fatalf("unauthorized request was forwarded: %s", event)
	case <-time.After(1200 * time.Millisecond):
	}
	docker(t, "stop", "--time", "10", name)
	if code := docker(t, "inspect", "--format", "{{.State.ExitCode}}", name); code != "0" {
		t.Fatalf("gateway did not shut down cleanly: exit %s", code)
	}
	logs := docker(t, "logs", name)
	if !regexp.MustCompile(`(?m)^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+0800\s`).MatchString(logs) {
		t.Fatalf("TZ=Asia/Shanghai was not honored by the production binary:\n%s", logs)
	}
	t.Logf("production timezone log: %s", strings.SplitN(logs, "\n", 2)[0])
	t.Log("validated read-only rootfs, native positive/negative probes, system CA, embedded timezone, TLS forwarding, bad HMAC/expired/missing auth, trusted identity, and graceful shutdown")
}

func assertScratchRuntime(t *testing.T, name string) {
	t.Helper()
	out, err := exec.Command("docker", "export", name).Output()
	if err != nil {
		t.Fatal(err)
	}
	archive := tar.NewReader(bytes.NewReader(out))
	hasCA := false
	for {
		header, err := archive.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		path := strings.TrimPrefix(header.Name, "/")
		switch filepath.Base(path) {
		case "sh", "bash", "curl":
			t.Errorf("scratch runtime retained external probe tool %s", path)
		}
		if strings.HasPrefix(path, "usr/share/zoneinfo/") {
			t.Errorf("scratch runtime retained filesystem timezone data: %s", path)
		}
		if path == "etc/ssl/certs/ca-certificates.crt" {
			pem, err := io.ReadAll(archive)
			if err != nil {
				t.Fatal(err)
			}
			hasCA = x509.NewCertPool().AppendCertsFromPEM(pem)
		}
	}
	if !hasCA {
		t.Fatal("runtime lost its system CA bundle")
	}
}

var trustedIdentity = map[string]string{
	ingestidentity.TrustedSpaceIDKey:      "smoke-space",
	ingestidentity.TrustedOrgIDKey:        "smoke-org",
	ingestidentity.TrustedUserIDKey:       "smoke-user",
	ingestidentity.TrustedKeyIDKey:        "smoke-key",
	ingestidentity.TrustedAgentIDKey:      "smoke-agent",
	ingestidentity.TrustedAgentProductKey: "Codex",
}

func signToken(exp int64) string {
	payload, _ := json.Marshal(map[string]any{"v": 2, "sp": "smoke-space", "org": "smoke-org", "u": "smoke-user", "kid": "smoke-key", "aid": "smoke-agent", "ap": "Codex", "iat": time.Now().Add(-2 * time.Minute).Unix(), "exp": exp})
	encoded := base64.RawURLEncoding.EncodeToString(payload)
	mac := hmac.New(sha256.New, []byte("runtime-smoke-secret"))
	mac.Write([]byte(encoded))
	return "Bearer " + encoded + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func request(signal, marker string, conn *grpc.ClientConn) ([]byte, func(context.Context) error) {
	var options []grpc.CallOption
	if marker == "grpc-zstd" {
		options = append(options, grpc.UseCompressor("zstd"))
	}
	spoof := func(attrs pcommon.Map) {
		attrs.PutStr("smoke.case", marker)
		attrs.PutStr("space.id", "untrusted-space")
		for key := range trustedIdentity {
			attrs.PutStr(key, "untrusted")
		}
	}
	switch signal {
	case "traces":
		req := ptraceotlp.NewExportRequest()
		resource := req.Traces().ResourceSpans().AppendEmpty()
		spoof(resource.Resource().Attributes())
		span := resource.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
		span.SetName("runtime-smoke")
		span.SetTraceID(pcommon.TraceID{1})
		span.SetSpanID(pcommon.SpanID{1})
		body, _ := req.MarshalProto()
		return body, func(ctx context.Context) error {
			_, err := ptraceotlp.NewGRPCClient(conn).Export(ctx, req, options...)
			return err
		}
	case "logs":
		req := plogotlp.NewExportRequest()
		resource := req.Logs().ResourceLogs().AppendEmpty()
		spoof(resource.Resource().Attributes())
		resource.ScopeLogs().AppendEmpty().LogRecords().AppendEmpty().Body().SetStr("runtime-smoke")
		body, _ := req.MarshalProto()
		return body, func(ctx context.Context) error {
			_, err := plogotlp.NewGRPCClient(conn).Export(ctx, req, options...)
			return err
		}
	default:
		req := pmetricotlp.NewExportRequest()
		resource := req.Metrics().ResourceMetrics().AppendEmpty()
		spoof(resource.Resource().Attributes())
		metric := resource.ScopeMetrics().AppendEmpty().Metrics().AppendEmpty()
		metric.SetName("runtime_smoke")
		metric.SetEmptyGauge().DataPoints().AppendEmpty().SetIntValue(1)
		body, _ := req.MarshalProto()
		return body, func(ctx context.Context) error {
			_, err := pmetricotlp.NewGRPCClient(conn).Export(ctx, req, options...)
			return err
		}
	}
}

func docker(t *testing.T, args ...string) string {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	out, err := exec.CommandContext(ctx, "docker", args...).CombinedOutput()
	if err != nil {
		t.Fatalf("docker %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}
