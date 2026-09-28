# Builds the whole app into one image: the React app is compiled, then copied
# into the backend, so FastAPI serves both the website and the API from one URL.

# ---------- stage 1: build the React app ----------
FROM node:20-slim AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build -- --outDir dist --emptyOutDir

# ---------- stage 2: the Python server ----------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# uv installs the exact versions recorded in uv.lock
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first: this layer stays cached while only your code changes
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev

# Application code, then the compiled website from stage 1
COPY backend/ ./
COPY --from=frontend /app/dist ./static

# Writable paths for conversation memory and the returns log
ENV CHECKPOINT_DB=/tmp/conversations.sqlite \
    RETURNS_LOG=/tmp/returns_log.json \
    HOST=0.0.0.0

EXPOSE 8000
CMD ["uv", "run", "--locked", "--no-dev", "python", "run.py"]
