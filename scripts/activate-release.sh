#!/usr/bin/env bash
# Atomically switch or roll back to an already verified Saturn release.

set -euo pipefail

COMMIT="${1:?usage: activate-release.sh COMMIT [DEPLOY_ROOT]}"
DEPLOY_ROOT="${2:-/home/coolhand/projects/saturn}"
python3 "$DEPLOY_ROOT/releases/$COMMIT/saturn/deployment.py" \
    activate "$COMMIT" "$DEPLOY_ROOT"
