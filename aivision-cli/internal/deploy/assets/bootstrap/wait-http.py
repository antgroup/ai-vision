#!/usr/bin/env python3
"""Wait for actual HTTP readiness without requiring tools in the target image."""
import sys
import time
import urllib.error
import urllib.request

def main():
    for attempt in range(200):
        try:
            with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
                if response.status == 200:
                    break
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(3)
    else:
        raise SystemExit("HTTP readiness timed out")


if __name__ == "__main__":
    main()
