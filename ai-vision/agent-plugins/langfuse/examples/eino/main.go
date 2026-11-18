package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
)

func main() {
	mode := flag.String("mode", "fixture", "fixture or live")
	ledgerPath := flag.String("ledger", "", "required output ledger path (contains synthetic trace IO, no credentials)")
	flag.Parse()
	if *ledgerPath == "" {
		fmt.Fprintln(os.Stderr, "-ledger is required")
		os.Exit(2)
	}
	cfg := config{Mode: *mode, Host: os.Getenv("LANGFUSE_HOST"), PublicKey: os.Getenv("LANGFUSE_PUBLIC_KEY"), SecretKey: os.Getenv("LANGFUSE_SECRET_KEY"), ModelBaseURL: os.Getenv("MODEL_BASE_URL"), ModelAPIKey: os.Getenv("MODEL_API_KEY"), ModelName: os.Getenv("MODEL_NAME")}
	result, err := runSuite(context.Background(), cfg)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := writeLedger(*ledgerPath, result); err != nil {
		fmt.Fprintln(os.Stderr, "could not write ledger")
		os.Exit(1)
	}
	fmt.Printf("Completed %d %s scenarios; ledger written. Run platform read-back verification separately.\n", len(result.Scenarios), result.Mode)
}

func writeLedger(path string, result ledger) error {
	file, err := os.CreateTemp(filepath.Dir(path), ".eino-ledger-*.json")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	encoder := json.NewEncoder(file)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(result); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(file.Name(), path)
}
