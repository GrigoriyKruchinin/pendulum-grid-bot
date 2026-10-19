"""Celery application and beat schedule."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from pendulum.config.settings import RedisSettings

celery_app = Celery("pendulum", broker=RedisSettings().url, include=["pendulum.workers.tasks"])
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    timezone="UTC",
    enable_utc=True,
    # Separate queues let each worker container hold only the secrets its tasks need.
    task_routes={
        "pendulum.sync_candles": {"queue": "data"},
        "pendulum.scan_pairs": {"queue": "data"},
        "pendulum.daily_summary": {"queue": "notify"},
        "pendulum.weekly_summary": {"queue": "notify"},
    },
    beat_schedule={
        "sync-candles": {"task": "pendulum.sync_candles", "schedule": 300.0},
        "weekly-scan": {
            "task": "pendulum.scan_pairs",
            "schedule": crontab(minute=0, hour=1, day_of_week="mon"),
        },
        "daily-paper-summary": {"task": "pendulum.daily_summary", "schedule": crontab(minute=5, hour=0)},
        "weekly-paper-summary": {
            "task": "pendulum.weekly_summary",
            "schedule": crontab(minute=15, hour=0, day_of_week="mon"),
        },
    },
)
