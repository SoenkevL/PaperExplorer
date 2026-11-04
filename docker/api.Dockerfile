FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages
RUN uv sync --frozen --no-dev --package paperexplorer-api
# The API runs Stages too: Tesseract's English model for OCR of scanned PDFs, pinned as in
# pipeline.Dockerfile, so OCR never downloads at run time.
ADD --checksum=sha256:8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba \
    https://github.com/tesseract-ocr/tessdata_best/raw/e12c65a915945e4c28e237a9b52bc4a8f39a0cec/eng.traineddata \
    /opt/tessdata/eng.traineddata
ENV PE_TESSDATA_DIR=/opt/tessdata
CMD ["uvicorn", "--factory", "paperexplorer_api.app:app_from_env", "--host", "0.0.0.0", "--port", "8000"]
