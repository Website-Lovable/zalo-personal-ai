import time

from celery import shared_task
from django.core.cache import cache

from .observability import HEARTBEAT_KEY


@shared_task
def heartbeat():
    """Run by beat every minute: proves broker, beat and a worker are all alive."""
    cache.set(HEARTBEAT_KEY, time.time(), timeout=24 * 3600)
    return True
