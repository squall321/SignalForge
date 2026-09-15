# 번역 마감시각이 NLP 단계의 무한 팽창을 막는지 검증한다.
import asyncio
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nlp import translator as T


@pytest.fixture(autouse=True)
def _clear_deadline():
    T.set_deadline(None)
    yield
    T.set_deadline(None)


def test_no_deadline_means_unlimited():
    assert not T.past_deadline()


def test_past_deadline_is_detected():
    T.set_deadline(time.monotonic() - 1)
    assert T.past_deadline()


def test_future_deadline_is_not_past():
    T.set_deadline(time.monotonic() + 60)
    assert not T.past_deadline()


@pytest.mark.asyncio
async def test_translation_skipped_after_deadline(monkeypatch):
    """마감을 넘기면 번역기를 부르지 않고 원문을 돌려줘야 한다."""
    called = []

    class _Spy:
        def __init__(self, *a, **kw):
            pass

        def translate(self, text):
            called.append(text)
            return "EN"

    monkeypatch.setattr(T, "GoogleTranslator", _Spy)
    T.set_deadline(time.monotonic() - 1)

    out = await T.translate_to_english("Bonjour le monde", source_lang="fr")
    assert out == "Bonjour le monde", "마감 후에도 번역 결과로 바꿨다"
    assert not called, "마감 후에도 번역기를 호출했다"


@pytest.mark.asyncio
async def test_translation_runs_before_deadline(monkeypatch):
    class _Ok:
        def __init__(self, *a, **kw):
            pass

        def translate(self, text):
            return "Hello world"

    monkeypatch.setattr(T, "GoogleTranslator", _Ok)
    monkeypatch.setattr(T, "_throttle", lambda: asyncio.sleep(0))
    T.set_deadline(time.monotonic() + 60)

    assert await T.translate_to_english("Bonjour", source_lang="fr") == "Hello world"


@pytest.mark.asyncio
async def test_long_text_stops_midway_at_deadline(monkeypatch):
    """긴 글은 조각 단위로도 마감을 확인해 남은 조각은 원문으로 둔다."""
    n_calls = {"n": 0}
    deadline = time.monotonic() + 0.25

    class _Slow:
        def __init__(self, *a, **kw):
            pass

        def translate(self, text):
            n_calls["n"] += 1
            time.sleep(0.15)
            return "X" * len(text)

    monkeypatch.setattr(T, "GoogleTranslator", _Slow)
    monkeypatch.setattr(T, "_throttle", lambda: asyncio.sleep(0))
    T.set_deadline(deadline)

    long_text = "bonjour tout le monde. " * 500      # _CHUNK(2000) 초과
    t0 = time.monotonic()
    await T.translate_to_english(long_text, source_lang="fr")
    elapsed = time.monotonic() - t0

    assert elapsed < 2.0, f"마감을 무시하고 {elapsed:.1f}s 돌았다"
    assert n_calls["n"] < len(T._split_chunks(long_text, T._CHUNK)), \
        "마감 후에도 모든 조각을 번역했다"


@pytest.mark.asyncio
async def test_pipeline_threads_deadline_to_translator(monkeypatch):
    """process_voc_list 가 마감을 번역기까지 전달하는지."""
    from nlp.pipeline import process_voc_list

    called = []

    class _Spy:
        def __init__(self, *a, **kw):
            pass

        def translate(self, text):
            called.append(text)
            return "EN"

    monkeypatch.setattr(T, "GoogleTranslator", _Spy)

    class _Voc:
        content_original = "Das ist ein deutscher Satz über ein Samsung Galaxy Telefon."
        content_translated = None
        language_detected = None
        sentiment_score = None
        sentiment_label = None
        categories = None
        engagement_score = None
        likes_count = comments_count = shares_count = 0

    await process_voc_list([_Voc()], translate_deadline=time.monotonic() - 1)
    assert not called, "마감이 지났는데 번역을 시도했다"


@pytest.mark.asyncio
async def test_deadline_stops_retry_backoff(monkeypatch):
    """마감은 '새 번역을 막는 것'만으로 부족하다 — 진행 중인 재시도도 끊어야 한다.

    백오프가 최대 30초씩 4회 붙으면 이미 시작된 한 건이 마감을 수 분 넘긴다.
    """
    attempts = {"n": 0}

    class _RateLimited:
        def __init__(self, *a, **kw):
            pass

        def translate(self, text):
            attempts["n"] += 1
            raise RuntimeError("too many requests")

    monkeypatch.setattr(T, "GoogleTranslator", _RateLimited)
    monkeypatch.setattr(T, "_throttle", lambda: asyncio.sleep(0))
    T.set_deadline(time.monotonic() - 1)          # 이미 지난 마감

    t0 = time.monotonic()
    out = await T._translate_chunk("Bonjour", "fr", "fr")
    elapsed = time.monotonic() - t0

    assert out == "Bonjour", "실패했는데 원문을 보존하지 않았다"
    assert attempts["n"] <= 1, f"마감 후에도 {attempts['n']}회 재시도했다"
    assert elapsed < 1.0, f"백오프로 {elapsed:.1f}s 를 썼다"


@pytest.mark.asyncio
async def test_deadline_stops_mymemory_pieces(monkeypatch):
    """MyMemory 500자 분할은 긴 글의 호출 수를 4배로 늘린다 —
    조각 루프 안에서도 마감을 봐야 한다."""
    calls = {"n": 0}

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        def translate(self, text):
            raise RuntimeError("google down")

    class _MM:
        def __init__(self, *a, **kw):
            pass

        def translate(self, text):
            calls["n"] += 1
            return "EN "

    monkeypatch.setattr(T, "GoogleTranslator", _Boom)
    monkeypatch.setattr(T, "MyMemoryTranslator", _MM)
    monkeypatch.setattr(T, "MYMEMORY_MAP", {"fr": "fr-FR"})
    monkeypatch.setattr(T, "_throttle", lambda: asyncio.sleep(0))
    T.set_deadline(time.monotonic() - 1)

    long_fr = "bonjour tout le monde. " * 90        # 약 2,070자 = 5조각
    out = await T._translate_chunk(long_fr, "fr", "fr")
    assert calls["n"] == 0, "마감 후에도 MyMemory 를 불렀다"
    assert out == long_fr
