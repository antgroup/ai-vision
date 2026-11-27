package blueprint

import (
	"strings"
	"testing"
)

func TestValidateNamespace(t *testing.T) {
	valid := []string{
		"aivision",
		"aiv-test",
		"aiv_test",
		"aiv.test",
		"a1",
		"0",
		"existing_",
		"Existing-Test",
		"trailing-",
		strings.Repeat("a", 64),
	}
	for _, namespace := range valid {
		t.Run("valid/"+namespace, func(t *testing.T) {
			if err := ValidateNamespace(namespace); err != nil {
				t.Fatalf("ValidateNamespace(%q) = %v, want nil", namespace, err)
			}
		})
	}

	invalid := []string{
		"",
		".",
		"..",
		"../victim",
		"..\\victim",
		"/absolute",
		"a/b",
		"a\\b",
		".hidden",
		"-leading",
		"_leading",
		"with space",
		"a$b",
	}
	for _, namespace := range invalid {
		t.Run("invalid/"+namespace, func(t *testing.T) {
			if err := ValidateNamespace(namespace); err == nil {
				t.Fatalf("ValidateNamespace(%q) = nil, want error", namespace)
			}
		})
	}
}

func TestBlueprintValidateRejectsUnsafeNamespace(t *testing.T) {
	bp := &Blueprint{
		Metadata: Metadata{Namespace: "../victim"},
		Config:   Config{SourceRoot: t.TempDir()},
		Components: []Component{
			{Name: "demo", Kind: "process", Run: "true"},
		},
	}
	if err := bp.Validate(); err == nil {
		t.Fatal("expected an unsafe namespace to be rejected")
	}
}

func TestBlueprintValidateDefaultsNamespace(t *testing.T) {
	bp := &Blueprint{
		Config: Config{SourceRoot: t.TempDir()},
		Components: []Component{
			{Name: "demo", Kind: "process", Run: "true"},
		},
	}
	if err := bp.Validate(); err != nil {
		t.Fatal(err)
	}
	if bp.Metadata.Namespace != "aivision" {
		t.Fatalf("namespace = %q, want aivision", bp.Metadata.Namespace)
	}
}
