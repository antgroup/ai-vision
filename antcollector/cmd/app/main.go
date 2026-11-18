// AI Vision's authenticated OTLP gateway: verifies the aivision ingest token,
// stamps the authoritative space/user identity, and forwards all signals over
// OTLP to the signoz-ob ingest endpoint. The gateway is the sole external
// data-plane writer; signoz-ob owns storage and retrieval.
package main

import (
	"log"
	"os"
	_ "time/tzdata"

	"github.com/open-telemetry/opentelemetry-collector-contrib/extension/pprofextension"
	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/exporter"
	"go.opentelemetry.io/collector/extension"
	"go.opentelemetry.io/collector/otelcol"
	"go.opentelemetry.io/collector/processor"
	"go.opentelemetry.io/collector/processor/batchprocessor"
	"go.opentelemetry.io/collector/processor/memorylimiterprocessor"
	"go.opentelemetry.io/collector/receiver"
	"go.opentelemetry.io/collector/receiver/otlpreceiver"

	"aivision/collector/exporter/boundedotlphttpexporter"
	"aivision/collector/extension/aivisionauthextension"
	"aivision/collector/processor/modelbillingprocessor"
	"aivision/collector/processor/spaceauthprocessor"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		if err := runHealthcheck(os.Args[2:]); err != nil {
			log.Fatal(err)
		}
		return
	}
	settings := otelcol.CollectorSettings{
		Factories: components,
		BuildInfo: component.BuildInfo{
			Command: "otelcorecol", Description: "AI Vision SigNoz OTLP gateway", Version: "0.96.0-dev",
		},
	}
	if err := otelcol.NewCommand(settings).Execute(); err != nil {
		log.Fatal(err)
	}
}

func components() (otelcol.Factories, error) {
	var err error
	factories := otelcol.Factories{}
	factories.Extensions, err = extension.MakeFactoryMap(pprofextension.NewFactory(), aivisionauthextension.NewFactory())
	if err != nil {
		return otelcol.Factories{}, err
	}
	factories.Receivers, err = receiver.MakeFactoryMap(otlpreceiver.NewFactory())
	if err != nil {
		return otelcol.Factories{}, err
	}
	factories.Processors, err = processor.MakeFactoryMap(
		memorylimiterprocessor.NewFactory(), spaceauthprocessor.NewFactory(),
		modelbillingprocessor.NewFactory(), batchprocessor.NewFactory(),
	)
	if err != nil {
		return otelcol.Factories{}, err
	}
	factories.Exporters, err = exporter.MakeFactoryMap(boundedotlphttpexporter.NewFactory())
	if err != nil {
		return otelcol.Factories{}, err
	}
	return factories, nil
}
