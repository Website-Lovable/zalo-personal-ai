"""Celery tasks for AIVA outbox delivery."""

from __future__ import annotations

from celery import shared_task


@shared_task(ignore_result=True)
def deliver_outbox_item(item_id: int):
    from .delivery import deliver_one

    return deliver_one(item_id)


@shared_task(ignore_result=True)
def deliver_due_outbox():
    from .delivery import deliver_due

    return deliver_due()
