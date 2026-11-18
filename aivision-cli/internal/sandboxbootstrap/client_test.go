package sandboxbootstrap

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

func TestMachineClientRetriesClaimAndEnrollmentWithoutCredentialMixing(t *testing.T) {
	plan := validPlan(t)
	var mu sync.Mutex
	attempts := map[string]int{}
	var enrolled []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		raw, _ := io.ReadAll(req.Body)
		mu.Lock()
		attempts[req.URL.Path]++
		count := attempts[req.URL.Path]
		mu.Unlock()
		if req.Header.Get("Cookie") != "" {
			t.Error("cookie sent to machine API")
		}
		if strings.HasSuffix(req.URL.Path, "/bootstrap") {
			if req.Header.Get("Authorization") != "Bearer private-bootstrap" || strings.Contains(string(raw), "private-") {
				t.Error("claim credential mixed with body")
			}
			if count == 1 {
				conn, _, _ := w.(http.Hijacker).Hijack()
				conn.Close()
				return
			}
			json.NewEncoder(w).Encode(map[string]any{"code": 0, "message": "success", "data": map[string]any{"plan": plan}})
			return
		}
		if req.Header.Get("Authorization") != "Bearer private-enrollment" {
			t.Error("enrollment used wrong capability")
		}
		mu.Lock()
		enrolled = append(enrolled, string(raw))
		mu.Unlock()
		if count == 1 {
			http.Error(w, "private-enrollment provider error", 503)
			return
		}
		io.WriteString(w, `{"code":0,"data":{"registration_state":"saved","connection_uuid":"0123456789abcdef0123456789abcdef"}}`)
	}))
	defer server.Close()
	client, err := NewClient(server.URL+"/api/public/sandbox-installations/01234567-89ab-cdef-0123-456789abcdef", "private-bootstrap", "private-enrollment")
	if err != nil {
		t.Fatal(err)
	}
	received, err := client.Claim(context.Background(), strings.Repeat("a", 32))
	if err != nil || received.PlanID != plan.PlanID {
		t.Fatalf("claim retry failed: %v", err)
	}
	if err = client.Enroll(context.Background(), strings.Repeat("a", 32), plan, "linux/arm64", "generated-private-api-key"); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(enrolled) != 2 || enrolled[0] != enrolled[1] {
		t.Fatalf("enrollment retry changed identity/key: %v", enrolled)
	}
}
func TestMachineClientRefusesRedirectAndSafeErrors(t *testing.T) {
	called := false
	destination := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { called = true }))
	defer destination.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		http.Redirect(w, req, destination.URL+"/credential", 307)
	}))
	defer server.Close()
	client, err := NewClient(server.URL+"/api/public/sandbox-installations/01234567-89ab-cdef-0123-456789abcdef", "private-bootstrap", "private-enrollment")
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.Claim(context.Background(), strings.Repeat("a", 32))
	if err == nil || called || strings.Contains(err.Error(), "private") || strings.Contains(err.Error(), server.URL) {
		t.Fatalf("redirect/secret leak: %t %v", called, err)
	}
	for _, url := range []string{"http://secret@host/api/public/sandbox-installations/01234567-89ab-cdef-0123-456789abcdef", "http://host/elsewhere", "http://host/api/public/sandbox-installations/01234567-89ab-cdef-0123-456789abcdef?token=private"} {
		if _, err = NewClient(url, "private-bootstrap", "private-enrollment"); err == nil {
			t.Fatal(fmt.Sprintf("invalid URL accepted %s", url))
		}
	}
}

func TestEnrollmentRequiresConfirmedSavedIdentity(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		io.WriteString(w, `{"code":0,"data":{"registration_state":"pending"}}`)
	}))
	defer server.Close()
	client, err := NewClient(server.URL+"/api/public/sandbox-installations/01234567-89ab-cdef-0123-456789abcdef", "private-bootstrap", "private-enrollment")
	if err != nil {
		t.Fatal(err)
	}
	if err = client.Enroll(context.Background(), strings.Repeat("a", 32), validPlan(t), "linux/arm64", "generated-private-api-key"); err == nil {
		t.Fatal("unconfirmed enrollment was treated as saved")
	}
}
