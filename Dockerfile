# Two stages, because the app needs Node to build and Python to run, and shipping a
# Node toolchain in the runtime image would triple its size for no benefit.
#
# Render builds this directly -- no build command to configure, and the same image runs
# locally, so "works on my machine" and "works on Render" are the same claim.

# --------------------------------------------------------------- frontend build
FROM node:22-slim AS frontend

WORKDIR /build
# Copy the manifests alone first: this layer is cached until a dependency actually
# changes, so editing a component does not reinstall node_modules.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ---------------------------------------------------------------------- runtime
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependency manifest first, again for layer caching. Installing with an empty package
# tree would fail, so the package directory is created before the install.
COPY pyproject.toml ./
RUN mkdir -p backend/app && touch backend/app/__init__.py \
    && pip install --no-cache-dir ".[postgres]"

COPY backend/ ./backend/
# The stub installed above has served its purpose. Removing it leaves the dependencies
# in site-packages and exactly one copy of `app`, the one on PYTHONPATH.
RUN pip uninstall -y stats-ev-solver

COPY --from=frontend /build/dist ./frontend/dist

# The app writes job logs and, in SQLite mode, its database here. On Render this is
# ephemeral unless a disk is mounted -- which is exactly why the deploy uses Postgres.
RUN mkdir -p data/logs

# Uvicorn reads $PORT via app.cli, so the platform decides the port.
ENV HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONPATH=/app/backend
EXPOSE 8000

# Not root: nothing here needs it, and a container that cannot overwrite its own code
# is one fewer thing to think about.
RUN useradd --create-home --uid 10001 solver && chown -R solver:solver /app
USER solver

CMD ["python", "-m", "app.cli", "serve"]
