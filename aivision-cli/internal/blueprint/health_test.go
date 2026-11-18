package blueprint

import (
	"testing"

	"gopkg.in/yaml.v3"
)

func TestNativeHealthcheckRejectsAmbiguousOrEmptyCommand(t *testing.T) {
	for _, health := range []string{
		"execArgv: []",
		"execArgv: ['/app/collector', 'healthcheck']\n      exec: echo ok",
		"execArgv: ['/app/collector', 'healthcheck']\n      http: http://localhost/",
		"execArgv: ['/app/collector', 'healthcheck']\n      tcp: localhost:80",
		"execArgv: ['', 'healthcheck']",
		"execArgv: ['   ', 'healthcheck']",
	} {
		t.Run(health, func(t *testing.T) {
			var bp Blueprint
			raw := "config:\n  sourceRoot: /tmp/source\ncomponents:\n  - name: gateway\n    kind: container\n    image: fixture\n    health:\n      " + health + "\n"
			if err := yaml.Unmarshal([]byte(raw), &bp); err != nil {
				t.Fatal(err)
			}
			if err := bp.Validate(); err == nil {
				t.Fatal("accepted an invalid native healthcheck")
			}
		})
	}
}
