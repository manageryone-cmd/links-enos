#!/bin/sh
# Build the container image and verify the stdio handshake inside it.
#
#   ./scripts/build_docker.sh [tag]
#
# Then clients can use:  python3 install_mcp.py --mode docker
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

TAG=${1:-links-enos:latest}
command -v docker >/dev/null 2>&1 || { echo "error: docker not found" >&2; exit 2; }

echo "Building $TAG ..."
docker build -t "$TAG" .

echo "Selftest inside the container ..."
docker run --rm --entrypoint python "$TAG" -m enos.mcp_server --selftest

echo "Checking the MCP handshake over stdio ..."
RESPONSE=$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"build-check","version":"0"}}}' \
  | docker run -i --rm "$TAG" 2>/dev/null)

printf '%s' "$RESPONSE" | grep -q '"serverInfo"' || {
    echo "error: initialize handshake failed" >&2
    printf '%s\n' "$RESPONSE" >&2
    exit 1
}

SIZE=$(docker image inspect "$TAG" --format '{{.Size}}')
echo "OK: $TAG (${SIZE} bytes)"
echo "Configure a client with:  python3 install_mcp.py --mode docker --image $TAG"
