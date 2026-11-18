package generate

import (
	"sort"
	"strings"
)

// yamlQuote renders s as a YAML double-quoted scalar, escaping the characters
// that would otherwise break parsing. Every generated string value goes through
// this so arbitrary env/port/path values stay valid YAML.
func yamlQuote(s string) string {
	r := strings.NewReplacer(
		`\`, `\\`,
		`"`, `\"`,
		"\n", `\n`,
		"\r", `\r`,
		"\t", `\t`,
	)
	return `"` + r.Replace(s) + `"`
}

// yamlFlowSeq renders a single-line YAML flow sequence of quoted strings,
// e.g. ["a", "b"].
func yamlFlowSeq(items []string) string {
	quoted := make([]string, 0, len(items))
	for _, it := range items {
		quoted = append(quoted, yamlQuote(it))
	}
	return "[" + strings.Join(quoted, ", ") + "]"
}

// sortedKeys returns the keys of a set in deterministic order.
func sortedKeys(set map[string]bool) []string {
	keys := make([]string, 0, len(set))
	for k := range set {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// k8sName sanitizes s into a DNS-1123 subdomain/label fragment: lowercased,
// runs of invalid characters collapsed to '-', trimmed of leading/trailing '-'.
func k8sName(s string) string {
	var b strings.Builder
	prevDash := false
	for _, r := range strings.ToLower(s) {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9':
			b.WriteRune(r)
			prevDash = false
		default:
			if !prevDash {
				b.WriteByte('-')
				prevDash = true
			}
		}
	}
	return strings.Trim(b.String(), "-")
}

// sensitiveKeyPatterns are env-key substrings whose values must never be written
// into generated materials in plaintext (Kubernetes Secrets get placeholders).
var sensitiveKeyPatterns = []string{
	"PASSWORD", "PASSWD", "SECRET", "TOKEN", "KEY", "DSN", "CREDENTIAL", "APIKEY", "API_KEY",
}

// isSensitiveKey reports whether an env key looks like it carries a credential.
func isSensitiveKey(k string) bool {
	ku := strings.ToUpper(k)
	for _, p := range sensitiveKeyPatterns {
		if strings.Contains(ku, p) {
			return true
		}
	}
	return false
}
