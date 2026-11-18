// aiv-http-probe performs a bounded HTTP readiness request without a script runtime.
package main

import (
	"fmt"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

func probe(host, port, path string, timeout time.Duration) error {
	n, err := strconv.Atoi(port)
	if err != nil || n < 1 || n > 65535 || host == "" || !strings.HasPrefix(path, "/") || timeout <= 0 {
		return fmt.Errorf("invalid probe arguments")
	}
	transport := &http.Transport{DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := &http.Client{
		Timeout:       timeout,
		Transport:     transport,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	req, err := http.NewRequest(http.MethodGet, "http://"+net.JoinHostPort(host, port)+path, nil)
	if err != nil {
		return fmt.Errorf("invalid probe request")
	}
	response, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("readiness request failed")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("readiness returned HTTP %d", response.StatusCode)
	}
	return nil
}

func run(args []string) error {
	if len(args) != 3 && len(args) != 4 {
		return fmt.Errorf("usage: aiv-http-probe HOST PORT PATH [TIMEOUT_SECONDS]")
	}
	timeout := 3 * time.Second
	if len(args) == 4 {
		seconds, err := strconv.ParseFloat(args[3], 64)
		if err != nil || seconds <= 0 || seconds > 300 {
			return fmt.Errorf("timeout must be greater than zero and at most 300 seconds")
		}
		timeout = time.Duration(seconds * float64(time.Second))
	}
	return probe(args[0], args[1], args[2], timeout)
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
