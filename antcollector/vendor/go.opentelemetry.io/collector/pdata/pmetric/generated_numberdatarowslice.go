// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

// Table compatibility derived from the public pdata v1.3.0 wrappers.

package pmetric

import (
	"sort"

	"go.opentelemetry.io/collector/pdata/internal"
	otlpmetrics "go.opentelemetry.io/collector/pdata/internal/data/protogen/metrics/v1"
)

// NumberDataRowSlice logically represents a slice of NumberDataRow.
//
// This is a reference type. If passed by value and callee modifies it, the
// caller will see the modification.
//
// Must use NewNumberDataRowSlice function to create new instances.
// Important: zero-initialized instance is not valid for use.
type NumberDataRowSlice struct {
	orig  *[]*otlpmetrics.NumberDataRow
	state *internal.State
}

func newNumberDataRowSlice(orig *[]*otlpmetrics.NumberDataRow, state *internal.State) NumberDataRowSlice {
	return NumberDataRowSlice{orig: orig, state: state}
}

// NewNumberDataRowSlice creates a NumberDataRowSlice with 0 elements.
// Can use "EnsureCapacity" to initialize with a given capacity.
func NewNumberDataRowSlice() NumberDataRowSlice {
	orig := []*otlpmetrics.NumberDataRow(nil)
	state := internal.StateMutable
	return newNumberDataRowSlice(&orig, &state)
}

// Len returns the number of elements in the slice.
//
// Returns "0" for a newly instance created with "NewNumberDataRowSlice()".
func (es NumberDataRowSlice) Len() int {
	return len(*es.orig)
}

// At returns the element at the given index.
//
// This function is used mostly for iterating over all the values in the slice:
//
//	for i := 0; i < es.Len(); i++ {
//	    e := es.At(i)
//	    ... // Do something with the element
//	}
func (es NumberDataRowSlice) At(i int) NumberDataRow {
	return newNumberDataRow((*es.orig)[i], es.state)
}

// EnsureCapacity is an operation that ensures the slice has at least the specified capacity.
// 1. If the newCap <= cap then no change in capacity.
// 2. If the newCap > cap then the slice capacity will be expanded to equal newCap.
//
// Here is how a new NumberDataRowSlice can be initialized:
//
//	es := NewNumberDataRowSlice()
//	es.EnsureCapacity(4)
//	for i := 0; i < 4; i++ {
//	    e := es.AppendEmpty()
//	    // Here should set all the values for e.
//	}
func (es NumberDataRowSlice) EnsureCapacity(newCap int) {
	es.state.AssertMutable()
	oldCap := cap(*es.orig)
	if newCap <= oldCap {
		return
	}

	newOrig := make([]*otlpmetrics.NumberDataRow, len(*es.orig), newCap)
	copy(newOrig, *es.orig)
	*es.orig = newOrig
}

// AppendEmpty will append to the end of the slice an empty NumberDataRow.
// It returns the newly added NumberDataRow.
func (es NumberDataRowSlice) AppendEmpty() NumberDataRow {
	es.state.AssertMutable()
	*es.orig = append(*es.orig, &otlpmetrics.NumberDataRow{})
	return es.At(es.Len() - 1)
}

// MoveAndAppendTo moves all elements from the current slice and appends them to the dest.
// The current slice will be cleared.
func (es NumberDataRowSlice) MoveAndAppendTo(dest NumberDataRowSlice) {
	es.state.AssertMutable()
	dest.state.AssertMutable()
	if *dest.orig == nil {
		// We can simply move the entire vector and avoid any allocations.
		*dest.orig = *es.orig
	} else {
		*dest.orig = append(*dest.orig, *es.orig...)
	}
	*es.orig = nil
}

// RemoveIf calls f sequentially for each element present in the slice.
// If f returns true, the element is removed from the slice.
func (es NumberDataRowSlice) RemoveIf(f func(NumberDataRow) bool) {
	es.state.AssertMutable()
	newLen := 0
	for i := 0; i < len(*es.orig); i++ {
		if f(es.At(i)) {
			continue
		}
		if newLen == i {
			// Nothing to move, element is at the right place.
			newLen++
			continue
		}
		(*es.orig)[newLen] = (*es.orig)[i]
		newLen++
	}
	*es.orig = (*es.orig)[:newLen]
}

// CopyTo copies all elements from the current slice overriding the destination.
func (es NumberDataRowSlice) CopyTo(dest NumberDataRowSlice) {
	dest.state.AssertMutable()
	srcLen := es.Len()
	destCap := cap(*dest.orig)
	if srcLen <= destCap {
		(*dest.orig) = (*dest.orig)[:srcLen:destCap]
		for i := range *es.orig {
			if (*dest.orig)[i] == nil {
				(*dest.orig)[i] = &otlpmetrics.NumberDataRow{}
			}
			newNumberDataRow((*es.orig)[i], es.state).CopyTo(newNumberDataRow((*dest.orig)[i], dest.state))
		}
		return
	}
	origs := make([]otlpmetrics.NumberDataRow, srcLen)
	wrappers := make([]*otlpmetrics.NumberDataRow, srcLen)
	for i := range *es.orig {
		wrappers[i] = &origs[i]
		newNumberDataRow((*es.orig)[i], es.state).CopyTo(newNumberDataRow(wrappers[i], dest.state))
	}
	*dest.orig = wrappers
}

// Sort sorts the NumberDataRow elements within NumberDataRowSlice given the
// provided less function so that two instances of NumberDataRowSlice
// can be compared.
func (es NumberDataRowSlice) Sort(less func(a, b NumberDataRow) bool) {
	es.state.AssertMutable()
	sort.SliceStable(*es.orig, func(i, j int) bool { return less(es.At(i), es.At(j)) })
}
