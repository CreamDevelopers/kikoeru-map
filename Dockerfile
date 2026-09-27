# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH

FROM base AS builder
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY pyproject.toml ./
RUN python -m venv /opt/venv \
 && python -c "import tomllib;d=tomllib.load(open('pyproject.toml','rb'));print('\n'.join(d['project']['dependencies']))" > requirements.txt \
 && python -c "import tomllib;d=tomllib.load(open('pyproject.toml','rb'));print('\n'.join(d['project']['optional-dependencies']['dev']))" > requirements-dev.txt \
 && pip install -r requirements.txt

FROM builder AS builder-dev
RUN pip install -r requirements-dev.txt

FROM base AS runtime
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libchromaprint-tools fonts-ipafont-gothic tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data/media /data/uploads /tmp/prometheus \
 && chown -R app:app /data /tmp/prometheus
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY alembic.ini gunicorn.conf.py pyproject.toml ./
COPY alembic ./alembic
COPY app ./app
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus \
    WEB_CONCURRENCY=2
USER app
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["web"]

FROM runtime AS test
USER root
COPY --from=builder-dev /opt/venv /opt/venv
COPY tests ./tests
USER app
ENV APP_ENV=test \
    RUFF_CACHE_DIR=/tmp/.ruff_cache \
    MYPY_CACHE_DIR=/tmp/.mypy_cache \
    PYTHONPYCACHEPREFIX=/tmp/pycache
CMD ["test"]
