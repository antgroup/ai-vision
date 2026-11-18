package cli

import "testing"

func TestParseLogsArgs(t *testing.T) {
	tests := []struct {
		name      string
		args      []string
		file      string
		component string
		lines     int
		follow    bool
	}{
		{
			name:      "blueprint flag after component",
			args:      []string{"be", "--file", "source.yaml", "-n", "5"},
			file:      "source.yaml",
			component: "be",
			lines:     5,
		},
		{
			name:      "short follow flag",
			args:      []string{"be", "-f", "--file", "source.yaml"},
			file:      "source.yaml",
			component: "be",
			lines:     50,
			follow:    true,
		},
		{
			name:      "short follow flag before component",
			args:      []string{"-f", "be"},
			component: "be",
			lines:     50,
			follow:    true,
		},
		{
			name:      "long follow flag",
			args:      []string{"be", "--file", "source.yaml", "--follow"},
			file:      "source.yaml",
			component: "be",
			lines:     50,
			follow:    true,
		},
		{
			name:      "kubernetes target with follow",
			args:      []string{"be", "--target", "kubernetes", "-f"},
			component: "be",
			lines:     50,
			follow:    true,
		},
		{
			name:      "flags before and after component",
			args:      []string{"-n", "5", "be", "--follow", "--file", "source.yaml"},
			file:      "source.yaml",
			component: "be",
			lines:     5,
			follow:    true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g, component, lines, follow, err := parseLogsArgs(tt.args)
			if err != nil {
				t.Fatal(err)
			}
			if g.file != tt.file || component != tt.component || lines != tt.lines || follow != tt.follow {
				t.Fatalf("parseLogsArgs(%q) = file=%q, component=%q, lines=%d, follow=%t", tt.args, g.file, component, lines, follow)
			}
		})
	}
}

func TestParseLogsArgsSupportsAllFlagsBeforeComponent(t *testing.T) {
	g, component, lines, follow, err := parseLogsArgs([]string{"-n", "5", "-F", "--file", "source.yaml", "be"})
	if err != nil {
		t.Fatal(err)
	}
	if g.file != "source.yaml" || component != "be" || lines != 5 || !follow {
		t.Fatalf("parseLogsArgs() = file=%q, component=%q, lines=%d, follow=%t", g.file, component, lines, follow)
	}
}

func TestParseLogsArgsRejectsBlueprintForKubernetes(t *testing.T) {
	if _, _, _, _, err := parseLogsArgs([]string{"be", "--target", "kubernetes", "--file", "blueprint.yaml"}); err == nil {
		t.Fatal("Kubernetes logs accepted a Docker blueprint")
	}
}
