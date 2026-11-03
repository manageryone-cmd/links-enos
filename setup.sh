#!/bin/sh
# links-enos MCP memory server - one-line setup for any client.
#
#   ./setup.sh                        # detect clients, configure the ones present
#   ./setup.sh --target all           # configure every supported client
#   ./setup.sh --mode docker          # zero Python dependency for the client
#   ./setup.sh --docker-build         # build the image first, then configure
#   ./setup.sh --uninstall --target all
#
# Requires nothing beyond `sh` and, for --mode python, a Python >= 3.8.
# POSIX sh on purpose: works in dash, busybox ash, Git Bash and macOS /bin/sh.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# optional: build the container image before installing
# ---------------------------------------------------------------------------
BUILD_IMAGE=0
if [ "${1:-}" = "--docker-build" ]; then
    BUILD_IMAGE=1
    shift
fi

if [ "$BUILD_IMAGE" -eq 1 ]; then
    command -v docker >/dev/null 2>&1 || die "docker not found; install Docker or use --mode python"
    say "Building image links-enos:latest ..."
    docker build -t links-enos:latest "$SCRIPT_DIR"
fi

# ---------------------------------------------------------------------------
# locate a Python interpreter (any of py / python3 / python)
# ---------------------------------------------------------------------------
PYTHON=${ENOS_PYTHON:-}
if [ -z "$PYTHON" ]; then
    for candidate in python3 python py; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON=$candidate
            break
        fi
    done
fi

if [ -z "$PYTHON" ]; then
    cat >&2 <<'EOF'
No Python interpreter found.

Pick one of these instead:

  docker build -t links-enos . && docker run -i --rm links-enos       # container
  # then configure clients with:  python3 install_mcp.py --mode docker   (needs Python once)
  # or point clients at a prebuilt single-file binary:  --mode binary --binary ./links-enos

EOF
    exit 2
fi

# Verify the interpreter is recent enough without emitting anything on stdout.
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' ; then
    die "$PYTHON is too old; links-enos needs Python >= 3.8"
fi
say "Using $("$PYTHON" -c 'import platform; print(platform.python_implementation() + " " + platform.python_version())')"

# ---------------------------------------------------------------------------
# hand over to the installer (all remaining flags are forwarded verbatim)
# ---------------------------------------------------------------------------
exec "$PYTHON" "$SCRIPT_DIR/install_mcp.py" "$@"
