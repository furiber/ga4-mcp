# Container for `ga4-mcp remote` (built by `gcloud run deploy --source .`, see deploy/cloud-run.sh).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . && useradd --create-home --uid 10001 app

USER app
# Cloud Run sets PORT (8080); the CLI reads HOST and PORT from the environment.
ENV HOST=0.0.0.0 PORT=8080
EXPOSE 8080
CMD ["ga4-mcp", "remote"]
