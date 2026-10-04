# Tollgate dashboard, hosted (Gemini only: no Ollama in the container).
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY . .
# The synthetic company database and the demo model files are built once, at image build time.
RUN uv run python -m erp.seed && uv run python -c "from evals.scenarios import ensure_model_files; ensure_model_files()"
ENV TOLLGATE_LOCAL_MODELS=off HOST=0.0.0.0
CMD ["uv", "run", "--frozen", "python", "-m", "dashboard.server"]
