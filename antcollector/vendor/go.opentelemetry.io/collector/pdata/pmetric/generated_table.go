// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

// Table compatibility derived from the public pdata v1.3.0 wrappers.

package pmetric

import (
	jsoniter "github.com/json-iterator/go"

	"go.opentelemetry.io/collector/pdata/internal"
	otlpmetrics "go.opentelemetry.io/collector/pdata/internal/data/protogen/metrics/v1"
)

// Table groups numeric measurements for each timestamp in one row.
//
// This is a reference type, if passed by value and callee modifies it the
// caller will see the modification.
//
// Must use NewTable function to create new instances.
// Important: zero-initialized instance is not valid for use.
type Table struct {
	orig  *otlpmetrics.Table
	state *internal.State
}

func newTable(orig *otlpmetrics.Table, state *internal.State) Table {
	return Table{orig: orig, state: state}
}

// NewTable creates a new empty Table.
//
// This must be used only in testing code. Users should use "AppendEmpty" when part of a Slice,
// OR directly access the member if this is embedded in another struct.
func NewTable() Table {
	state := internal.StateMutable
	return newTable(&otlpmetrics.Table{}, &state)
}

// MoveTo moves all properties from the current struct overriding the destination and
// resetting the current instance to its zero value
func (ms Table) MoveTo(dest Table) {
	ms.state.AssertMutable()
	dest.state.AssertMutable()
	*dest.orig = *ms.orig
	*ms.orig = otlpmetrics.Table{}
}

// DataRows returns the DataRows associated with this Table.
func (ms Table) DataRows() NumberDataRowSlice {
	return newNumberDataRowSlice(&ms.orig.DataRows, ms.state)
}

// CopyTo copies all properties from the current struct overriding the destination.
func (ms Table) CopyTo(dest Table) {
	dest.state.AssertMutable()
	ms.DataRows().CopyTo(dest.DataRows())
}

// Table returns the Table data for a Metric. It is valid only when Type is MetricTypeTable.
func (ms Metric) Table() Table {
	value, ok := ms.orig.GetData().(*otlpmetrics.Metric_Table)
	if !ok {
		return Table{}
	}
	return newTable(value.Table, ms.state)
}

// SetEmptyTable replaces the Metric data with an empty Table.
func (ms Metric) SetEmptyTable() Table {
	ms.state.AssertMutable()
	value := &otlpmetrics.Table{}
	ms.orig.Data = &otlpmetrics.Metric_Table{Table: value}
	return newTable(value, ms.state)
}

func (ms Table) unmarshalTable(iter *jsoniter.Iterator) {
	iter.ReadObjectCB(func(iter *jsoniter.Iterator, field string) bool {
		if field == "data_rows" || field == "dataRows" {
			iter.ReadArrayCB(func(iter *jsoniter.Iterator) bool {
				ms.DataRows().AppendEmpty().unmarshalRow(iter)
				return true
			})
		} else {
			iter.Skip()
		}
		return true
	})
}
