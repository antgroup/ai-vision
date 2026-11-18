// Package components maps product roles to stable deployment identities.
// Physical resources and blueprint/state keys retain their existing names.
package components

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/assets"
)

const ProductImageRegistry = "ghcr.io/antgroup/ai-vision"

type Kubernetes struct {
	Kind        string `json:"kind"`
	Name        string `json:"name"`
	LogSelector string `json:"logSelector,omitempty"`
}

type Component struct {
	ID             string            `json:"id"`
	DisplayName    string            `json:"displayName"`
	Implementation string            `json:"implementation"`
	LegacyName     string            `json:"legacyName"`
	Aliases        []string          `json:"aliases"`
	DockerName     string            `json:"dockerName"`
	Kubernetes     Kubernetes        `json:"kubernetes"`
	ConfigAliases  map[string]string `json:"configAliases"`
}

var catalog = load()

func load() []Component {
	data, err := assets.Files.ReadFile("components.json")
	if err != nil {
		panic(err)
	}
	var document struct {
		SchemaVersion int         `json:"schemaVersion"`
		Components    []Component `json:"components"`
	}
	if err := json.Unmarshal(data, &document); err != nil {
		panic(fmt.Errorf("embedded component registry: %w", err))
	}
	if document.SchemaVersion != 1 {
		panic("unsupported embedded component registry version")
	}
	return document.Components
}

// All returns the components in deployment order. Callers must not mutate them.
func All() []Component { return catalog }

// ServiceImageComponents associates each product service with its image variable.
func ServiceImageComponents() map[string]Component {
	images := make(map[string]Component, len(catalog)-1)
	for _, component := range catalog {
		if component.ID == "database" {
			continue
		}
		for role, legacy := range component.ConfigAliases {
			if strings.HasSuffix(role, "_IMAGE") {
				images[legacy] = component
			}
		}
	}
	return images
}

func (c Component) ImageName(registry string) string {
	if strings.TrimSuffix(registry, "/") == ProductImageRegistry {
		return c.ID
	}
	return c.DockerName
}

func (c Component) DefaultImageTemplate() string {
	return "${IMAGE_REGISTRY}/" + c.ID + ":${IMAGE_TAG}"
}

func Lookup(name string) (Component, bool) {
	for _, c := range catalog {
		if name == c.ID || name == c.LegacyName || name == c.DockerName || name == c.Kubernetes.Name {
			return c, true
		}
		for _, alias := range c.Aliases {
			if name == alias {
				return c, true
			}
		}
	}
	return Component{}, false
}

// Canonical leaves custom blueprint component names unchanged.
func Canonical(name string) string {
	if c, ok := Lookup(name); ok {
		return c.ID
	}
	return name
}

// Legacy resolves a selector to the existing blueprint and state identity.
func Legacy(name string) string {
	if c, ok := Lookup(name); ok {
		return c.LegacyName
	}
	return name
}

// VarKey resolves a product-role config key to the implementation key consumed
// by existing blueprints. It does not rename upstream runtime environment keys.
func VarKey(key string) string {
	for _, c := range catalog {
		if old, ok := c.ConfigAliases[key]; ok {
			return old
		}
	}
	return key
}

func DisplayVar(key string) string {
	for _, c := range catalog {
		for role, old := range c.ConfigAliases {
			if old == key {
				return role
			}
		}
	}
	return key
}

// NormalizeVars normalizes one precedence layer before it is merged. Role keys
// win over legacy keys within the same map. Repeatable CLI flags instead call
// VarKey while parsing, so the last flag wins even across different spellings.
func NormalizeVars(values map[string]string) map[string]string {
	out := make(map[string]string, len(values))
	for key, value := range values {
		out[key] = value
	}
	for _, c := range catalog {
		for role, old := range c.ConfigAliases {
			if value, ok := values[role]; ok {
				out[old] = value
				delete(out, role)
			}
		}
	}
	return out
}

// AddRoleReferences allows custom blueprint templates to use role variables
// too. Their values follow the implementation key through normal expansion.
func AddRoleReferences(values map[string]string) {
	for _, c := range catalog {
		for role, old := range c.ConfigAliases {
			if _, ok := values[old]; ok {
				values[role] = "${" + old + "}"
			}
		}
	}
}
