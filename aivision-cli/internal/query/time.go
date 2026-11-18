// Package query is a client for the SigNoz "main" query API (POST
// /api/v5/query_range), the unified querier behind traces, logs and metrics.
//
// It builds v5 composite builder-queries and normalises the response envelope
// ({status,data:{results:[...]}}) into plain rows or metric series. Field names
// follow the query-builder (snake_case) schema, e.g. trace_id, span_id, name,
// duration_nano for traces and timestamp, id, severity_text, body for logs.
package query

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

// ParseTime interprets a user time expression as epoch milliseconds. Accepted
// forms:
//
//	""                  -> def (caller-supplied default)
//	1712345678000       -> epoch milliseconds (all digits, length >= 12)
//	1712345678          -> epoch seconds (all digits, length <= 10)
//	2026-09-16T10:00:00Z / RFC3339 (with or without zone)
//	90s | 15m | 6h | 2d | 1w  (relative to now; "ago" suffix allowed)
//	"now"               -> now
func ParseTime(s string, def time.Time, now time.Time) (int64, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return def.UnixMilli(), nil
	}
	lower := strings.ToLower(s)
	if lower == "now" {
		return now.UnixMilli(), nil
	}
	// Pure digits: epoch ms or s.
	if isAllDigits(s) {
		n, err := strconv.ParseInt(s, 10, 64)
		if err != nil {
			return 0, fmt.Errorf("invalid time %q", s)
		}
		if len(s) >= 12 {
			return n, nil // milliseconds
		}
		return n * 1000, nil // seconds
	}
	// Relative duration, optionally suffixed with "ago".
	rel := strings.TrimSuffix(lower, "ago")
	rel = strings.TrimSpace(rel)
	if d, ok := parseDurationDays(rel); ok {
		return now.Add(-d).UnixMilli(), nil
	}
	// RFC3339, then a couple of common layouts.
	for _, layout := range []string{time.RFC3339Nano, time.RFC3339, "2006-01-02T15:04:05", "2006-01-02 15:04:05", "2006-01-02"} {
		if t, err := time.ParseInLocation(layout, s, time.Local); err == nil {
			return t.UnixMilli(), nil
		}
	}
	return 0, fmt.Errorf("invalid time %q (want RFC3339, epoch ms/s, or a relative duration like 90m/6h/2d)", s)
}

// parseDurationDays extends time.ParseDuration with d (day) and w (week).
func parseDurationDays(s string) (time.Duration, bool) {
	if s == "" {
		return 0, false
	}
	last := s[len(s)-1]
	num := s[:len(s)-1]
	switch last {
	case 'd':
		if n, err := strconv.ParseFloat(num, 64); err == nil {
			return time.Duration(n * float64(24*time.Hour)), true
		}
	case 'w':
		if n, err := strconv.ParseFloat(num, 64); err == nil {
			return time.Duration(n * float64(7*24*time.Hour)), true
		}
	}
	if d, err := time.ParseDuration(s); err == nil {
		return d, true
	}
	return 0, false
}

func isAllDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// ResolveRange turns --since/--until into an inclusive [start,end] in epoch ms.
// Empty since defaults to now-1h; empty until defaults to now. It validates that
// start < end.
func ResolveRange(since, until string, now time.Time) (int64, int64, error) {
	start, err := ParseTime(since, now.Add(-time.Hour), now)
	if err != nil {
		return 0, 0, fmt.Errorf("--since: %w", err)
	}
	end, err := ParseTime(until, now, now)
	if err != nil {
		return 0, 0, fmt.Errorf("--until: %w", err)
	}
	if end <= start {
		return 0, 0, fmt.Errorf("--until (%d) must be after --since (%d)", end, start)
	}
	return start, end, nil
}
