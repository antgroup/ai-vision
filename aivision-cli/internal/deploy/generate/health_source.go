//go:build !native_distribution

package generate

import "fmt"

func sourceHTTPHealth(endpoint string) []string {
	return []string{"python", "-c", "import urllib.request; r=urllib.request.urlopen(" + fmt.Sprintf("%q", endpoint) + ",timeout=3); assert r.status==200"}
}
