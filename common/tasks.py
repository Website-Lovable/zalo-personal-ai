import time

from celery import shared_task
from django.core.cache import cache

from .observability import HEARTBEAT_KEY


@shared_task
def heartbeat():
    """Run by beat every minute: proves broker, beat and a worker are all alive."""
    cache.set(HEARTBEAT_KEY, time.time(), timeout=24 * 3600)
    return True


@shared_task(ignore_result=True)
def compute_ops_snapshots():
    """Run by beat every hour: measured daily figures, pushed out when OPS_PUSH_URL is set."""
    from .ops_report import push, refresh

    rows = refresh()
    push(rows)
    return len(rows)
