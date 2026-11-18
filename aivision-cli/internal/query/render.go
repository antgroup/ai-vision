package query

import (
	"encoding/json"
	"sort"
)

// listKeys are the conventional container keys the OpenAPI uses for a collection
// inside `data`. The first non-empty array of objects wins.
var listKeys = []string{
	"list", "traces", "spans", "sessions", "events", "items",
	"rows", "results", "data", "records", "logs",
}

// Row is one generic result row.
type Row map[string]any

// ExtractRows best-effort pulls the array of row objects out of an OpenAPI
// `data` payload. It handles: a top-level array; an object with a known list
// key; and an object whose values include an array of objects. Returns nil when
// no array of objects can be found (callers then fall back to raw JSON).
func ExtractRows(data json.RawMessage) []Row {
	if len(data) == 0 {
		return nil
	}
	// Top-level array.
	var arr []map[string]any
	if err := json.Unmarshal(data, &arr); err == nil {
		return toRows(arr)
	}
	// Object: try known keys first, then any array-of-objects value.
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(data, &obj); err != nil {
		return nil
	}
	for _, k := range listKeys {
		if raw, ok := obj[k]; ok {
			if rows := extractArray(raw); rows != nil {
				return rows
			}
			// Nested one level deeper (e.g. data.data.list).
			if inner := ExtractRows(raw); inner != nil {
				return inner
			}
		}
	}
	// Deterministic fallback: scan keys alphabetically for an array of objects.
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		if rows := extractArray(obj[k]); rows != nil {
			return rows
		}
	}
	return nil
}

func extractArray(raw json.RawMessage) []Row {
	var arr []map[string]any
	if err := json.Unmarshal(raw, &arr); err != nil {
		return nil
	}
	return toRows(arr)
}

func toRows(arr []map[string]any) []Row {
	if len(arr) == 0 {
		return nil
	}
	rows := make([]Row, 0, len(arr))
	for _, m := range arr {
		rows = append(rows, Row(m))
	}
	return rows
}

// CollectColumns returns the ordered union of keys across rows, preferring the
// columns seen in the first row, then any extras alphabetically.
func CollectColumns(rows []Row) []string {
	seen := map[string]bool{}
	var cols []string
	add := func(k string) {
		if !seen[k] {
			seen[k] = true
			cols = append(cols, k)
		}
	}
	for i, r := range rows {
		keys := make([]string, 0, len(r))
		for k := range r {
			keys = append(keys, k)
		}
		if i > 0 {
			sort.Strings(keys)
		}
		for _, k := range keys {
			add(k)
		}
	}
	return cols
}
