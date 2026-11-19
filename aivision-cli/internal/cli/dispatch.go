// Package cli implements the aiv command tree and owns process exit codes.
package cli

import (
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"
)

// Version is stamped from the repository root VERSION file by the build.
var Version = "dev"

// Run dispatches os.Args[1:] and exits the process with the conventional code
// (0 success, 1 runtime error, 2 usage error).
func Run(embeddedBlueprint string) {
	configurePlatform(embeddedBlueprint)
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	cmd, args := os.Args[1], os.Args[2:]
	var err error
	switch cmd {
	case "auth":
		err = cmdAuth(args)
	case "space":
		err = cmdSpace(args)
	case "key":
		err = cmdKey(args)
	case "query":
		err = cmdQuery(args)
	case "version", "--version":
		fmt.Printf("aiv %s\n", Version)
	case "help", "--help", "-h":
		usage()
	default:
		var known bool
		known, err = platformCommand(cmd, args)
		if !known {
			fmt.Fprintf(os.Stderr, "unknown command %q\n\n", cmd)
			usage()
			os.Exit(2)
		}
	}
	if errors.Is(err, flag.ErrHelp) {
		return
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "\naiv: %v\n", err)
		os.Exit(1)
	}
}

// varFlags accumulates repeatable string values in command flags.
type varFlags []string

func (v *varFlags) String() string     { return strings.Join(*v, ",") }
func (v *varFlags) Set(s string) error { *v = append(*v, s); return nil }

// splitInterspersedFlags moves recognized flags and their values ahead of
// positional arguments so Go's flag parser accepts options on either side.
func splitInterspersedFlags(fs *flag.FlagSet, args []string) (flags, positionals []string) {
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if arg == "--" {
			positionals = append(positionals, args[i+1:]...)
			break
		}
		if len(arg) < 2 || arg[0] != '-' || arg == "-" {
			positionals = append(positionals, arg)
			continue
		}

		name := strings.TrimLeft(arg, "-")
		if equal := strings.IndexByte(name, '='); equal >= 0 {
			name = name[:equal]
		}
		flags = append(flags, arg)
		f := fs.Lookup(name)
		if f == nil || strings.Contains(arg, "=") {
			continue
		}
		if boolFlag, ok := f.Value.(interface{ IsBoolFlag() bool }); ok && boolFlag.IsBoolFlag() {
			continue
		}
		if i+1 < len(args) {
			i++
			flags = append(flags, args[i])
		}
	}
	return flags, positionals
}
