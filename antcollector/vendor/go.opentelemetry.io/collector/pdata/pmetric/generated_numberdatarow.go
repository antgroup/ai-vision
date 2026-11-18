// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

// Table compatibility derived from the public pdata v1.3.0 wrappers.

package pmetric

import (
	jsoniter "github.com/json-iterator/go"

	"go.opentelemetry.io/collector/pdata/internal"
	otlpmetrics "go.opentelemetry.io/collector/pdata/internal/data/protogen/metrics/v1"
	"go.opentelemetry.io/collector/pdata/internal/json"
	"go.opentelemetry.io/collector/pdata/pcommon"
)

// NumberDataRow contains the sample time and named measurements for a Table row.
//
// This is a reference type, if passed by value and callee modifies it the
// caller will see the modification.
//
// Must use NewNumberDataRow function to create new instances.
// Important: zero-initialized instance is not valid for use.
type NumberDataRow struct {
	orig  *otlpmetrics.NumberDataRow
	state *internal.State
}

func newNumberDataRow(orig *otlpmetrics.NumberDataRow, state *internal.State) NumberDataRow {
	return NumberDataRow{orig: orig, state: state}
}

// NewNumberDataRow creates a new empty NumberDataRow.
//
// This must be used only in testing code. Users should use "AppendEmpty" when part of a Slice,
// OR directly access the member if this is embedded in another struct.
func NewNumberDataRow() NumberDataRow {
	state := internal.StateMutable
	return newNumberDataRow(&otlpmetrics.NumberDataRow{}, &state)
}

// MoveTo moves all properties from the current struct overriding the destination and
// resetting the current instance to its zero value
func (ms NumberDataRow) MoveTo(dest NumberDataRow) {
	ms.state.AssertMutable()
	dest.state.AssertMutable()
	*dest.orig = *ms.orig
	*ms.orig = otlpmetrics.NumberDataRow{}
}

// Attributes returns the Attributes associated with this NumberDataRow.
func (ms NumberDataRow) Attributes() pcommon.Map {
	return pcommon.Map(internal.NewMap(&ms.orig.Attributes, ms.state))
}

// StartTimestamp returns the starttimestamp associated with this NumberDataRow.
func (ms NumberDataRow) StartTimestamp() pcommon.Timestamp {
	return pcommon.Timestamp(ms.orig.StartTimeUnixNano)
}

// SetStartTimestamp replaces the starttimestamp associated with this NumberDataRow.
func (ms NumberDataRow) SetStartTimestamp(v pcommon.Timestamp) {
	ms.state.AssertMutable()
	ms.orig.StartTimeUnixNano = uint64(v)
}

// Timestamp returns the timestamp associated with this NumberDataRow.
func (ms NumberDataRow) Timestamp() pcommon.Timestamp {
	return pcommon.Timestamp(ms.orig.TimeUnixNano)
}

// SetTimestamp replaces the timestamp associated with this NumberDataRow.
func (ms NumberDataRow) SetTimestamp(v pcommon.Timestamp) {
	ms.state.AssertMutable()
	ms.orig.TimeUnixNano = uint64(v)
}

// ValueMap returns this row's named measurements.
func (ms NumberDataRow) ValueMap() pcommon.Map {
	return pcommon.Map(internal.NewMap(&ms.orig.ValueMap, ms.state))
}

// Exemplars returns the Exemplars associated with this NumberDataRow.
func (ms NumberDataRow) Exemplars() ExemplarSlice {
	return newExemplarSlice(&ms.orig.Exemplars, ms.state)
}

// Flags returns the flags associated with this NumberDataRow.
func (ms NumberDataRow) Flags() DataPointFlags {
	return DataPointFlags(ms.orig.Flags)
}

// SetFlags replaces the flags associated with this NumberDataRow.
func (ms NumberDataRow) SetFlags(v DataPointFlags) {
	ms.state.AssertMutable()
	ms.orig.Flags = uint32(v)
}

// CopyTo copies all properties from the current struct overriding the destination.
func (ms NumberDataRow) CopyTo(dest NumberDataRow) {
	dest.state.AssertMutable()
	ms.Attributes().CopyTo(dest.Attributes())
	dest.SetStartTimestamp(ms.StartTimestamp())
	dest.SetTimestamp(ms.Timestamp())
	ms.ValueMap().CopyTo(dest.ValueMap())
	ms.Exemplars().CopyTo(dest.Exemplars())
	dest.SetFlags(ms.Flags())
}

func (ms NumberDataRow) unmarshalRow(iter *jsoniter.Iterator) {
	iter.ReadObjectCB(func(iter *jsoniter.Iterator, field string) bool {
		switch field {
		case "start_time_unix_nano", "startTimeUnixNano":
			ms.orig.StartTimeUnixNano = json.ReadUint64(iter)
		case "time_unix_nano", "timeUnixNano":
			ms.orig.TimeUnixNano = json.ReadUint64(iter)
		case "attributes":
			iter.ReadArrayCB(func(iter *jsoniter.Iterator) bool {
				ms.orig.Attributes = append(ms.orig.Attributes, json.ReadAttribute(iter))
				return true
			})
		case "value_map", "valueMap":
			iter.ReadArrayCB(func(iter *jsoniter.Iterator) bool {
				ms.orig.ValueMap = append(ms.orig.ValueMap, json.ReadAttribute(iter))
				return true
			})
		case "exemplars":
			iter.ReadArrayCB(func(iter *jsoniter.Iterator) bool {
				ms.Exemplars().AppendEmpty().unmarshalJsoniter(iter)
				return true
			})
		case "flags":
			ms.orig.Flags = json.ReadUint32(iter)
		default:
			iter.Skip()
		}
		return true
	})
}
