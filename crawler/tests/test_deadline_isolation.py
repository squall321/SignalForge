# 크롤러가 건 번역 마감이 다음 태스크로 새지 않는지 검증한다.
import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nlp import translator as T


def test_deadline_does_not_leak_between_asyncio_runs():
    """celery 워커는 같은 스레드에서 태스크를 잇달아 돈다.

    크롤러가 건 마감이 다음 태스크(번역 backfill)로 새면 그쪽이 조용히
    아무것도 번역하지 않고 끝난다 — 되레 백로그를 영구화한다.
    """
    async def crawler_task():
        T.set_deadline(time.monotonic() - 1)      # 이미 지난 마감
        assert T.past_deadline()

    async def backfill_task():
        return T.past_deadline()

    asyncio.run(crawler_task())
    assert asyncio.run(backfill_task()) is False, \
        "앞 태스크의 마감이 다음 태스크로 샜다"


def test_deadline_does_not_leak_to_caller_thread():
    assert not T.past_deadline(), "스레드 기본 컨텍스트가 오염됐다"


def test_deadline_persists_within_one_run():
    """한 실행 안에서는 청크를 넘어가도 마감이 유지돼야 한다."""
    async def one_run():
        T.set_deadline(time.monotonic() + 60)
        first = T.past_deadline()
        await asyncio.sleep(0)
        second = T.past_deadline()
        return first, second

    assert asyncio.run(one_run()) == (False, False)
