FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.6
COPY pyproject.toml uv.lock README.md alembic.ini ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic ./alembic
COPY src ./src
RUN uv sync --frozen --no-dev

RUN groupadd --gid 10001 village-insight \
    && useradd --create-home --uid 10001 --gid 10001 village-insight \
    && mkdir -p /data/uploads /data/import /data/secrets \
    && chown -R village-insight:village-insight /data /app
USER village-insight

EXPOSE 8000
CMD ["uvicorn", "village_insight.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
