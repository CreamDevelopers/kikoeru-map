#!/bin/sh
set -eu

case "${1:-web}" in
  web)
    alembic upgrade head
    exec gunicorn -c gunicorn.conf.py app.main:app
    ;;
  worker)
    exec arq app.worker.WorkerSettings
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  test)
    ruff check app tests
    ruff format --check app tests
    mypy
    exec pytest -q
    ;;
  *)
    exec "$@"
    ;;
esac
