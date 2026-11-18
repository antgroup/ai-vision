// Command buildversion reads the AI Vision project version for CLI builds.
package main

import (
	"fmt"
	"os"
	"regexp"
	"strings"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	if len(os.Args) != 2 {
		return fmt.Errorf("usage: buildversion <VERSION>")
	}
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		return fmt.Errorf("read AI Vision version: %w", err)
	}
	version := strings.TrimSpace(string(data))
	if !regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z]+([.-][0-9A-Za-z]+)*)?$`).MatchString(version) {
		return fmt.Errorf("invalid AI Vision project version %q", version)
	}
	fmt.Println(version)
	return nil
}
