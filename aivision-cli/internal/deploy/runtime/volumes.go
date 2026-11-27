package runtime

import (
	"encoding/csv"
	"fmt"
	"path"
	"regexp"
	"strconv"
	"strings"
)

var dockerVolumeName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]*$`)

func namedVolumeTarget(name, target string) (string, error) {
	target = strings.ReplaceAll(target, `\`, "/")
	if !path.IsAbs(target) || path.Clean(target) == "/" {
		return "", fmt.Errorf("Docker volume %s requires an absolute container target other than /", name)
	}
	return name, nil
}

// volumeName extracts a named source from Docker's -v source:target[:mode].
func volumeName(spec string) (string, error) {
	name, destination, ok := strings.Cut(spec, ":")
	if !ok || !dockerVolumeName.MatchString(name) {
		return "", nil
	}
	target, mode, _ := strings.Cut(destination, ":")
	if mode != "" {
		// Each category of volume mode can be specified once.
		seen := map[string]bool{}
		for option := range strings.SplitSeq(mode, ",") {
			var category string
			switch option {
			case "ro", "rw":
				category = "access"
			case "z", "Z":
				category = "label"
			case "consistent", "cached", "delegated":
				category = "consistency"
			case "nocopy":
				category = "copy"
			default:
				return "", fmt.Errorf("invalid Docker -v mode for volume %s", name)
			}
			if seen[category] {
				return "", fmt.Errorf("conflicting Docker -v modes for volume %s", name)
			}
			seen[category] = true
		}
	}
	return namedVolumeTarget(name, target)
}

// Docker --mount uses CSV. A driver option containing commas is one field.
// Only select an explicit volume source with an unambiguous mount declaration.
func mountVolumeName(spec string) (string, error) {
	records, err := csv.NewReader(strings.NewReader(strings.TrimSpace(spec))).ReadAll()
	if err != nil || len(records) != 1 {
		return "", fmt.Errorf("invalid Docker --mount specification")
	}
	kind, source, target := "volume", "", ""
	validVolumeOptions := true
	for _, field := range records[0] {
		key, value, hasValue := strings.Cut(field, "=")
		if key != strings.TrimSpace(key) || hasValue && (value == "" || value != strings.TrimSpace(value)) {
			return "", fmt.Errorf("invalid Docker --mount field")
		}
		switch strings.ToLower(key) {
		case "type":
			if !hasValue {
				return "", fmt.Errorf("missing Docker --mount type")
			}
			kind = strings.ToLower(value)
		case "source", "src":
			if !hasValue {
				return "", fmt.Errorf("missing Docker --mount source")
			}
			source = value
		case "target", "dst", "destination":
			target = value
		case "ro", "readonly", "volume-nocopy":
			if hasValue {
				if _, err := strconv.ParseBool(value); err != nil {
					validVolumeOptions = false
				}
			}
		case "volume-subpath":
			clean := path.Clean(value)
			if !hasValue || path.IsAbs(clean) || clean == ".." || strings.HasPrefix(clean, "../") {
				validVolumeOptions = false
			}
		case "consistency", "volume-driver", "volume-label", "volume-opt":
			if !hasValue {
				validVolumeOptions = false
			}
		default:
			validVolumeOptions = false
		}
	}
	if kind != "volume" || source == "" {
		return "", nil
	}
	if !validVolumeOptions || !dockerVolumeName.MatchString(source) {
		return "", fmt.Errorf("invalid Docker named volume mount")
	}
	return namedVolumeTarget(source, target)
}
