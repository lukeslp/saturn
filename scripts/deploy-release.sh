#!/usr/bin/env bash
# Build and atomically activate one immutable, commit-addressed Saturn release.

set -euo pipefail

COMMIT="${1:?usage: deploy-release.sh COMMIT [DEPLOY_ROOT] [REPO]}"
DEPLOY_ROOT="${2:-/home/coolhand/projects/saturn}"
REPO="${3:-$(pwd)}"
COMMIT="$(git -C "$REPO" rev-parse --verify "$COMMIT^{commit}")"
RELEASES="$DEPLOY_ROOT/releases"
VENVS="$DEPLOY_ROOT/venvs"
RELEASE="$RELEASES/$COMMIT"
VENV="$VENVS/$COMMIT"
mkdir -p "$RELEASES" "$VENVS"
STAGE="$(mktemp -d "$RELEASES/.${COMMIT}.release.XXXXXX")"
VENV_STAGE="$(mktemp -d "$VENVS/.${COMMIT}.venv.XXXXXX")"

cleanup() {
    rm -rf "$STAGE" "$VENV_STAGE"
}
trap cleanup EXIT

if [[ -e "$RELEASE" || -L "$RELEASE" || -e "$VENV" || -L "$VENV" ]]; then
    echo "release or environment already exists for $COMMIT" >&2
    exit 1
fi

git -C "$REPO" archive "$COMMIT" | tar -x -C "$STAGE"
python3 "$STAGE/saturn/deployment.py" record "$COMMIT" "$STAGE" --repo "$REPO"
python3 "$STAGE/saturn/deployment.py" verify "$STAGE"

rmdir "$VENV_STAGE"
python3 -m venv --copies "$VENV_STAGE"
"$VENV_STAGE/bin/pip" install --disable-pip-version-check "$STAGE[web,llm]"
VENV_PYTHON="$VENV_STAGE/bin/python"
if [[ -L "$VENV_PYTHON" ]]; then
    REAL_PYTHON="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$VENV_PYTHON")"
    cp "$REAL_PYTHON" "$VENV_PYTHON.regular"
    chmod 755 "$VENV_PYTHON.regular"
    rm "$VENV_PYTHON"
    mv "$VENV_PYTHON.regular" "$VENV_PYTHON"
fi
if [[ ! -f "$VENV_PYTHON" || -L "$VENV_PYTHON" ]]; then
    echo "release environment requires a regular bin/python executable" >&2
    exit 1
fi

mv "$VENV_STAGE" "$VENV"
mv "$STAGE" "$RELEASE"
chmod -R a-w "$RELEASE" "$VENV"
python3 "$RELEASE/saturn/deployment.py" activate "$COMMIT" "$DEPLOY_ROOT"
trap - EXIT

echo "activated $COMMIT"
