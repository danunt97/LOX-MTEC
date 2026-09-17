#!/usr/bin/env python3
"""Docker HEALTHCHECK: ask the container's own /healthz endpoint."""

import json
import os
import sys
import urllib.error
import urllib.request

PORT = os.environ.get("LOXMTEC_WEB_PORT", "8080")
URL = f"http://127.0.0.1:{PORT}/healthz"

try:
    with urllib.request.urlopen(URL, timeout=8) as response:
        body = json.loads(response.read().decode("utf-8"))
        if response.status == 200 and body.get("status") == "ok":
            sys.exit(0)
        print(f"unhealthy: {body}", file=sys.stderr)
except urllib.error.HTTPError as err:
    print(f"unhealthy: HTTP {err.code} - {err.read().decode('utf-8', 'replace')[:200]}", file=sys.stderr)
except Exception as err:  # connection refused, timeout, invalid JSON
    print(f"unhealthy: {err}", file=sys.stderr)

sys.exit(1)
