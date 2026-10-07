FROM python:3.12-slim

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

# WARNING: v0.1 HTTP mode has NO authentication (OAuth 2.1 arrives in v1.0).
# Publish this port only on localhost or a trusted private network.
CMD ["helionyx", "serve", "--transport", "http", "--host", "0.0.0.0", "--port", "8080"]
