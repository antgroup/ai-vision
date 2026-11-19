package pdata_test

import (
	"testing"

	"github.com/stretchr/testify/require"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/pmetric/pmetricotlp"
	otlpmetrics "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	"google.golang.org/protobuf/encoding/protowire"
	"google.golang.org/protobuf/proto"
)

func TestTableMetricWireAndCopy(t *testing.T) {
	const payload = `{"resourceMetrics":[{"scopeMetrics":[{"metrics":[
		{"name":"table","metadata":[{"key":"source","value":{"stringValue":"fixture"}}],"table":{"dataRows":[{
			"startTimeUnixNano":"12","timeUnixNano":"34","flags":1,
			"attributes":[{"key":"zone","value":{"stringValue":"test"}}],
			"valueMap":[{"key":"requests","value":{"intValue":"5"}}],
			"exemplars":[{"timeUnixNano":"25","asInt":"2"}]
		}]}},
		{"name":"gauge","gauge":{"dataPoints":[{"asInt":"7"}]}}
	]}]}]}`
	request := pmetricotlp.NewExportRequest()
	require.NoError(t, request.UnmarshalJSON([]byte(payload)))
	assertTableRow(t, request.Metrics())

	// Check the legacy oneof tag with the standard OTLP protobuf decoder,
	// then reproduce the payload through the Table-aware pdata decoder.
	wire, err := request.MarshalProto()
	require.NoError(t, err)
	var standard otlpmetrics.ExportMetricsServiceRequest
	require.NoError(t, proto.Unmarshal(wire, &standard))
	standardMetric := standard.ResourceMetrics[0].ScopeMetrics[0].Metrics[0]
	require.Len(t, standardMetric.GetMetadata(), 1)
	require.Equal(t, "source", standardMetric.GetMetadata()[0].GetKey())
	require.Equal(t, "fixture", standardMetric.GetMetadata()[0].GetValue().GetStringValue())
	unknown := standardMetric.ProtoReflect().GetUnknown()
	field, wireType, tagLength := protowire.ConsumeTag(unknown)
	require.Positive(t, tagLength)
	require.Equal(t, protowire.Number(13), field)
	require.Equal(t, protowire.BytesType, wireType)
	roundTrip := pmetricotlp.NewExportRequest()
	require.NoError(t, roundTrip.UnmarshalProto(wire))
	roundTripJSON, err := roundTrip.MarshalJSON()
	require.NoError(t, err)
	require.NoError(t, roundTrip.UnmarshalJSON(roundTripJSON))
	assertTableRow(t, roundTrip.Metrics())

	cloned := pmetric.NewMetrics()
	request.Metrics().CopyTo(cloned)
	sourceTable := request.Metrics().ResourceMetrics().At(0).ScopeMetrics().At(0).Metrics().At(0).Table()
	preallocated := pmetric.NewTable()
	preallocated.DataRows().EnsureCapacity(2)
	sourceTable.CopyTo(preallocated)
	sourceTable.DataRows().At(0).ValueMap().PutInt("requests", 9)
	assertTableRow(t, cloned)
	require.Equal(t, 1, preallocated.DataRows().Len())
	value, present := preallocated.DataRows().At(0).ValueMap().Get("requests")
	require.True(t, present)
	require.EqualValues(t, 5, value.Int())
}

func assertTableRow(t *testing.T, metrics pmetric.Metrics) {
	t.Helper()
	require.Equal(t, 2, metrics.DataPointCount())
	samples := metrics.ResourceMetrics().At(0).ScopeMetrics().At(0).Metrics()
	require.Equal(t, pmetric.MetricTypeGauge, samples.At(1).Type())
	require.Equal(t, pmetric.MetricTypeTable, samples.At(0).Type())
	source, present := samples.At(0).Metadata().Get("source")
	require.True(t, present)
	require.Equal(t, "fixture", source.Str())
	rows := samples.At(0).Table().DataRows()
	require.Equal(t, 1, rows.Len())
	row := rows.At(0)
	require.EqualValues(t, 12, row.StartTimestamp())
	require.EqualValues(t, 34, row.Timestamp())
	require.EqualValues(t, 1, row.Flags())
	zone, present := row.Attributes().Get("zone")
	require.True(t, present)
	require.Equal(t, "test", zone.Str())
	value, present := row.ValueMap().Get("requests")
	require.True(t, present)
	require.EqualValues(t, 5, value.Int())
	require.Equal(t, 1, row.Exemplars().Len())
	require.EqualValues(t, 25, row.Exemplars().At(0).Timestamp())
	require.EqualValues(t, 2, row.Exemplars().At(0).IntValue())
}
