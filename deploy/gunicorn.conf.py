"""Production ASGI server: gunicorn managing uvicorn workers (HTTP + WebSocket).

One daphne process tops out at about 50 req/s with 2 s latency on 4 vCPU in the Phase 5
load test; 3 uvicorn workers gave ~2x the throughput at a 36 ms median. Tune with env vars.
"""

import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.environ.get("WEB_CONCURRENCY", min(4, multiprocessing.cpu_count())))
# Longer than Caddy's upstream keep-alive so the proxy never reuses a connection we closed.
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "75"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = 30
# Recycle workers now and then: caps slow memory growth without downtime.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "5000"))
max_requests_jitter = 500
accesslog = None  # Caddy writes the access log
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
forwarded_allow_ips = "*"  # only Caddy can reach us (internal Docker network)
