# syntax=docker/dockerfile:1

# The app is pure Python, so the build stage always runs on the build
# machine's own platform. Multi-arch builds don't run uv under emulation.
FROM --platform=$BUILDPLATFORM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
COPY docs ./docs
RUN uv sync --locked && uv run pytest -q && uv run python scripts/gen_tool_docs.py --check
# Release builds pass the version from the Git tag (e.g. 1.2.3).
ARG VERSION
RUN if [ -n "$VERSION" ]; then uv version --frozen "$VERSION"; fi \
 && rm -rf .venv && uv sync --frozen --no-dev --no-editable --compile-bytecode

FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.title="bluelink-mcp" \
      org.opencontainers.image.description="MCP server for Hyundai Bluelink" \
      org.opencontainers.image.source="https://github.com/mattoddie/bluelink-mcp" \
      org.opencontainers.image.documentation="https://bluelink-mcp.mattoddie.dev/docs/" \
      org.opencontainers.image.licenses="MIT"
RUN useradd --create-home --uid 1000 app && mkdir /data && chown app /data
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=http \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8080 \
    BLUELINK_TOKEN_FILE=/data/token.json
USER app
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request; os.environ.get('MCP_TRANSPORT')=='stdio' and sys.exit(0); urllib.request.urlopen('http://127.0.0.1:'+os.environ['MCP_HTTP_PORT']+'/health')"]
ENTRYPOINT ["bluelink-mcp"]
