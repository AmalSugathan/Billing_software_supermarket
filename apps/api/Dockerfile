FROM python:3.14-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.23
COPY pyproject.toml uv.lock ./
COPY apps/api/src ./apps/api/src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim AS runtime
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY apps/api/migrations ./apps/api/migrations
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
CMD ["uvicorn", "supermarket.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
