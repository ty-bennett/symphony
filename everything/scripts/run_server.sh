#!/usr/bin/env bash
set -euo pipefail
# One worker owns one backbone. Bind to loopback until auth/TLS is supplied by the harness.
exec python -m uvicorn edge_router.api.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
