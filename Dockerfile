# VIVATLAS — self-contained image.
#   docker build -t vivatlas:latest .
#   docker run -d -p 8710:8710 -v vivatlas_data:/data vivatlas:latest
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATABASE_URL=sqlite:////data/vivatlas.db

WORKDIR /app

# Install the package. Templates, static assets, fonts and avatars are bundled
# in the wheel, so no separate copy of them is needed at runtime.
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

# Build identity, stamped by CI (.github/workflows/docker.yml) and shown in
# Admin so a running container can be matched to a specific build. Placed after
# the pip install so changing the build number doesn't invalidate that layer.
# Harmless empty defaults for a plain local `docker build`.
ARG VIVATLAS_BUILD_VERSION=""
ARG VIVATLAS_BUILD_SHA=""
ARG VIVATLAS_BUILD_DATE=""
ENV VIVATLAS_BUILD_VERSION=$VIVATLAS_BUILD_VERSION \
    VIVATLAS_BUILD_SHA=$VIVATLAS_BUILD_SHA \
    VIVATLAS_BUILD_DATE=$VIVATLAS_BUILD_DATE

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# The unpacked browser extension, served as a one-click zip from Settings → Browser
# extension. Only the runtime files reach here (see .dockerignore). Resolved at runtime
# from /app/extension (the working dir).
COPY extension ./extension

# Non-root user and a data dir for the SQLite db + generated secret.
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /data \
    && useradd -m -u 1000 app \
    && chown -R app:app /app /data

USER app
EXPOSE 8710

# Healthy once the catalog answers HTTP (unauthenticated / redirects to setup).
HEALTHCHECK --interval=30s --timeout=6s --start-period=25s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8710/', timeout=5)"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
