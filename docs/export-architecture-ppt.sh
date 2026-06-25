#!/usr/bin/env bash
# Regenerate architecture-plan.pptx from architecture-plan.html
set -euo pipefail
DOCS="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DOCS/export-architecture-ppt.py"
