#!/usr/bin/env bash
# One command. Brings up the broker, runs the negative controls and a small measured matrix.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || make install
make demo
