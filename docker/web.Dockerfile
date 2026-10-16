FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages
RUN uv sync --frozen --no-dev --package paperexplorer-web
CMD ["uvicorn", "--factory", "paperexplorer_web.app:app_from_env", "--host", "0.0.0.0", "--port", "8080"]
