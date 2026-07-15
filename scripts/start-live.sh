#!/usr/bin/env bash
# Compatibility wrapper: the default README entrypoint now supports live code.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/start.sh" "$@"
