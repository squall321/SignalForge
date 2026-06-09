"""SignalForge Celery beat 에 등록할 수 있는 AX Hub 동기화 task.

사용 (SignalForge `app/services/celery_app.py` 등에서):

    from integrations.aidatahub.celery_task import aidatahub_sync_recent
    # celery_app.tasks 에 자동 등록됨 (@shared_task 데코레이터)

    celery_app.conf.beat_schedule = {
        ...,
        "aidatahub-sync-30min": {
            "task": "aidatahub.sync_recent",
            "schedule": crontab(minute="*/30"),
            "kwargs": {"since_minutes": 35},   # 5분 overlap
        },
    }

    celery -A app.services.celery_app beat
    celery -A app.services.celery_app worker -Q default

옵션 (즉시성 필요 시):

    아래 ``aidatahub_sync_immediate(voc_id)`` 를 VOC insert/publish 직후
    ``self.delay(voc_id)`` 로 호출하면 단건만 push.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from celery import shared_task

from .aidatahub_sync import (
    fetch_via_db,
    fetch_via_http,
    push_to_aidh,
    voc_to_record,
    _load_config,
)

logger = logging.getLogger(__name__)

# config 위치 — SF 의 환경에 맞게 변경 (또는 환경변수로)
DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yml"


def _run_async(coro):
    """celery worker 가 event loop 없을 때 안전한 실행."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # nested 시 thread 분리 권장 — 단순화: 새 loop
            return asyncio.run(coro)
    except RuntimeError:
        pass
    return asyncio.run(coro)


@shared_task(name="aidatahub.sync_recent", bind=True, max_retries=3, default_retry_delay=120)
def aidatahub_sync_recent(self, since_minutes: int = 35, dry_run: bool = False):
    """매 N분 cron 으로 호출되는 증분 동기화.

    Args:
        since_minutes: 현재 시점에서 (N분 전) ~ now 사이의 VOC.
        dry_run: 진짜 push 안 함 (체크 용).
    """
    try:
        return asyncio.run(
            _do_sync(since_minutes=since_minutes, all_mode=False, dry_run=dry_run)
        )
    except Exception as exc:
        logger.exception("aidatahub_sync_recent failed: %s", exc)
        raise self.retry(exc=exc)


@shared_task(name="aidatahub.sync_all", bind=True, max_retries=2, default_retry_delay=600)
def aidatahub_sync_all(self, dry_run: bool = False):
    """초기 backfill — 모든 processed VOC 일괄 push.

    매우 무거움. 운영 시작 1회만 사용.
    """
    try:
        return asyncio.run(_do_sync(since_minutes=None, all_mode=True, dry_run=dry_run))
    except Exception as exc:
        logger.exception("aidatahub_sync_all failed: %s", exc)
        raise self.retry(exc=exc)


async def _do_sync(*, since_minutes: int | None, all_mode: bool, dry_run: bool):
    cfg = _load_config(DEFAULT_CONFIG_PATH)
    aidh = cfg.get("aidatahub") or {}
    sf = cfg.get("signalforge") or {}
    sync_cfg = cfg.get("sync") or {}

    if all_mode:
        since = None
    elif since_minutes is not None:
        since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    else:
        since = None

    # 데이터 수집 — DB 우선, 없으면 HTTP
    if sf.get("db_url"):
        raw = await fetch_via_db(sf["db_url"], since=since)
    else:
        raw = await fetch_via_http(
            sf["base_url"], sf.get("api_key", ""),
            product_codes=sync_cfg.get("product_codes") or [],
            since=since,
            page_size=sync_cfg.get("page_size", 100),
            max_rps=sync_cfg.get("max_rps", 4.0),
        )

    logger.info("aidatahub sync fetched=%s mode=%s", len(raw), "all" if all_mode else "recent")

    # 필터 + 변환
    filt = sync_cfg.get("filter") or {}
    records = []
    for voc in raw:
        if filt.get("require_processed_at", True) and not voc.get("processed_at"):
            continue
        if filt.get("skip_when_pii_unmasked", True) and voc.get("pii_masked") is False:
            continue
        records.append(voc_to_record(voc))

    summary = await push_to_aidh(
        records,
        aidh_base_url=aidh["base_url"],
        aidh_api_key=aidh.get("api_key") or "",
        batch_size=sync_cfg.get("batch_size", 100),
        dry_run=dry_run,
    )
    logger.info(
        "aidatahub sync push: ok=%s failed=%s batches=%s",
        summary["ok"], summary["failed"], summary["batches"],
    )
    return {
        "fetched": len(raw),
        "pushed": len(records),
        **summary,
        "dead_letter_count": len(summary.get("dead_letter") or []),
    }
