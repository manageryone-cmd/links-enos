#!/bin/sh
# Build the single-file `links-enos` binary and smoke-test it.
#
#   ./scripts/build_binary.sh
#
# Output: dist/links-enos  (Linux/macOS; run the script on each OS to get that
# platform's artifact - PyInstaller cannot cross-compile).
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

PYTHON=${PYTHON:-python3}
command -v "$PYTHON" >/dev/null 2>&1 || { echo "error: $PYTHON not found" >&2; exit 2; }

"$PYTHON" - "$@" <<'PY'
import sys
if sys.version_info < (3, 8):
    sys.exit("error: Python >= 3.8 required")
PY

if ! "$PYTHON" -c "import PyInstaller, sys; sys.exit(0 if int(PyInstaller.__version__.split('.')[0]) >= 6 else 1)" 2>/dev/null; then
    echo "Installing PyInstaller >= 6 ..."
    "$PYTHON" -m pip install --quiet --upgrade "pyinstaller>=6.0"
fi

echo "Running tests before packaging ..."
"$PYTHON" -m unittest discover -s tests -p 'test_*.py' -q || {
    echo "error: tests failed; refusing to package" >&2
    exit 1
}

echo "Building dist/links-enos ..."
rm -rf build dist
"$PYTHON" -m PyInstaller --clean --noconfirm enos.spec

BIN=./dist/links-enos
[ -x "$BIN" ] || { echo "error: $BIN was not produced" >&2; exit 1; }

TMPDB=$(mktemp -d)/memory.db
echo "Smoke-testing the binary ..."
"$BIN" --db "$TMPDB" --selftest
"$BIN" --version

SIZE=$(wc -c < "$BIN" | tr -d ' ')
echo "OK: $BIN (${SIZE} bytes)"
echo "Configure a client with:  python3 install_mcp.py --mode binary --binary ./dist/links-enos"
