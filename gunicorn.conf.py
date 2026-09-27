import os
import shutil

bind = os.environ.get("BIND", "0.0.0.0:8000")
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
worker_class = "uvicorn.workers.UvicornWorker"
keepalive = 75
timeout = 60
graceful_timeout = 30
max_requests = 5000
max_requests_jitter = 500
accesslog = None
errorlog = "-"
forwarded_allow_ips = "*"
# /app は読み取り専用
control_socket = "/tmp/gunicorn.ctl"
proxy_headers = True


def on_starting(server):  # type: ignore[no-untyped-def]
    d = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if d:
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)


def child_exit(server, worker):  # type: ignore[no-untyped-def]
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)
