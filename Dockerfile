FROM python:3.12-slim

LABEL io.modelcontextprotocol.server.name="io.github.Jayzilva/helionyx"       org.opencontainers.image.source="https://github.com/Jayzilva/helionyx"       org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HNX_WORKSPACE=/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 helionyx \
    && mkdir -p /data && chown helionyx:helionyx /data

USER helionyx
VOLUME ["/data"]
EXPOSE 8080

# Default: MCP over stdio (docker run -i). For Streamable HTTP, override the command and set
# HNX_API_KEY; the server refuses a non-local bind without it (see docker-compose.yml).
CMD ["helionyx", "serve"]
