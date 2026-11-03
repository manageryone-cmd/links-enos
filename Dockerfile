# syntax=docker/dockerfile:1
#
# links-enos MCP memory server — stdio transport in a container.
#
#   docker build -t links-enos .
#   docker run -i --rm -v "$PWD/.enos:/data" links-enos   # one volume per project
#
# The MCP client launches it with `docker run -i --rm ... links-enos`; the `-i`
# flag is mandatory (the stream *is* the transport) and `-t` must NOT be used.

FROM python:3.12-slim

LABEL org.opencontainers.image.title="links-enos" \
      org.opencontainers.image.description="links-enos MCP memory server (stdio)" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    ENOS_MEMORY_DB=/data/memory.db \
    HOME=/data

WORKDIR /app

# No pip install: the package is standard-library only, so copying it is enough
# and the build has no network step.
COPY enos/ /app/enos/
COPY pyproject.toml /app/pyproject.toml

RUN mkdir -p /data && chmod 0777 /data

VOLUME ["/data"]

# `docker run -i links-enos` with no arguments starts the stdio server.
ENTRYPOINT ["python", "-m", "enos.mcp_server"]
