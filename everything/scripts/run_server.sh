#!/usr/bin/env bash
set -euo pipefail
# One worker owns one backbone. Bind to loopback until auth/TLS is supplied by the harness.
exec python -m edge_router.api.server "$@"
