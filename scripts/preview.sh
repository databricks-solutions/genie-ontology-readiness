#!/usr/bin/env bash
# Sanctioned isolated contributor previews; see preview.py --help.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$script_dir/preview.py" "$@"
