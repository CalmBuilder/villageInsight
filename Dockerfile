FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN uv pip install --system .

RUN useradd --create-home --uid 10001 village-insight \
    && mkdir -p /data/uploads /data/import \
    && chown -R village-insight:village-insight /data /app
USER village-insight

EXPOSE 8000
CMD ["uvicorn", "village_insight.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
