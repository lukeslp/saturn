#!/usr/bin/env bash
# Replace a venv's Python symlink with a drift-detecting regular launcher.

set -euo pipefail

VENV_PYTHON="${1:?usage: materialize-venv-python.sh VENV_PYTHON}"
if [[ -L "$VENV_PYTHON" ]]; then
    REAL_PYTHON="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$VENV_PYTHON")"
    HASH_COMMAND="$(command -v sha256sum)"
    EXPECTED_HASH="$("$HASH_COMMAND" "$REAL_PYTHON")"
    EXPECTED_HASH="${EXPECTED_HASH%% *}"
    printf '#!/usr/bin/env bash\nset -euo pipefail\nTARGET=%q\nEXPECTED_HASH=%q\nHASH_COMMAND=%q\nACTUAL_HASH="$("$HASH_COMMAND" "$TARGET")"\nACTUAL_HASH="${ACTUAL_HASH%%%% *}"\nif [[ "$ACTUAL_HASH" != "$EXPECTED_HASH" ]]; then\n    echo "Saturn runtime interpreter changed after deployment" >&2\n    exit 126\nfi\nexec -a "$0" "$TARGET" "$@"\n' \
        "$REAL_PYTHON" "$EXPECTED_HASH" "$HASH_COMMAND" > "$VENV_PYTHON.regular"
    chmod 755 "$VENV_PYTHON.regular"
    rm "$VENV_PYTHON"
    mv "$VENV_PYTHON.regular" "$VENV_PYTHON"
fi
if [[ ! -f "$VENV_PYTHON" || -L "$VENV_PYTHON" ]]; then
    echo "release environment requires a regular bin/python executable" >&2
    exit 1
fi
