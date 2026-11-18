//go:build native_distribution

package generate

func sourceHTTPHealth(endpoint string) []string {
	panic("source health probes are unavailable in a native distribution")
}
