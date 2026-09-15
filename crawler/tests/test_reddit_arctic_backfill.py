# Reddit 역사 백필의 커서·이어받기·소진 판정을 검증한다 — 틀리면 데이터를 잃는다.
import asyncio
import importlib
import json
import pathlib
import sys
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def _load(monkeypatch, tmp_path, **env):
    """env 를 세팅한 상태로 모듈을 새로 읽는다 (상수가 import 시점에 굳는다)."""
    env.setdefault("REDDIT_ARCTIC_STATE", str(tmp_path / "state.json"))
    env.setdefault("REDDIT_ARCTIC_DELAY", "0")
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    import reddit_arctic_backfill as m
    return importlib.reload(m)


def _item(ts: int, i: str):
    return {"permalink": f"/r/samsung/comments/{i}/x/", "created_utc": ts,
            "title": f"t{i}", "selftext": "", "id": i, "author": "a"}


def _ts(y, mo, d):
    return int(datetime(y, mo, d, tzinfo=timezone.utc).timestamp())


# ── 커서 전진 ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cursor_walks_backwards_and_exhausts(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, REDDIT_ARCTIC_MAX_PAGES=10)
    pages = [
        [_item(_ts(2023, 6, 10), "a"), _item(_ts(2023, 6, 5), "b")],
        [_item(_ts(2023, 3, 2), "c")],
        [],                                   # 더 없음 → 소진
    ]
    calls = []

    async def fake_get(client, params):
        calls.append(params["before"])
        return pages.pop(0) if pages else []

    monkeypatch.setattr(m, "_get", fake_get)
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, tzinfo=timezone.utc)
    posts, cursor = await m._walk_sub(None, "samsung", start, end)

    assert len(posts) == 3, "받은 글을 다 모으지 못했다"
    assert cursor is None, "소진했는데 커서를 남겼다"
    # before 가 단조 감소해야 한다 — 아니면 같은 구간을 맴돈다
    assert calls == sorted(calls, reverse=True), f"커서가 되돌아갔다: {calls}"


@pytest.mark.asyncio
async def test_page_cap_returns_resume_cursor(monkeypatch, tmp_path):
    """상한에 걸리면 반드시 커서를 남겨야 한다 — 없으면 그 해의 나머지를 잃는다."""
    m = _load(monkeypatch, tmp_path, REDDIT_ARCTIC_MAX_PAGES=2)
    seq = [_ts(2023, 12, 20), _ts(2023, 12, 10), _ts(2023, 12, 1)]
    n = {"i": 0}

    async def fake_get(client, params):
        i = n["i"]; n["i"] += 1
        return [_item(seq[min(i, len(seq) - 1)], f"p{i}")]

    monkeypatch.setattr(m, "_get", fake_get)
    posts, cursor = await m._walk_sub(
        None, "samsung",
        datetime(2023, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 1, tzinfo=timezone.utc))

    assert len(posts) == 2
    assert cursor is not None, "상한에 걸렸는데 커서가 없다 — 나머지를 영영 놓친다"
    assert cursor.year == 2023 and cursor.month == 12


@pytest.mark.asyncio
async def test_duplicate_timestamps_do_not_loop(monkeypatch, tmp_path):
    """같은 시각만 돌아와도 무한루프에 빠지면 안 된다."""
    m = _load(monkeypatch, tmp_path, REDDIT_ARCTIC_MAX_PAGES=50)
    same = _ts(2023, 5, 5)

    async def fake_get(client, params):
        return [_item(same, "same")]          # 항상 같은 글

    monkeypatch.setattr(m, "_get", fake_get)
    posts, cursor = await asyncio.wait_for(
        m._walk_sub(None, "samsung",
                    datetime(2023, 1, 1, tzinfo=timezone.utc),
                    datetime(2024, 1, 1, tzinfo=timezone.utc)),
        timeout=5)
    assert len(posts) == 1, "같은 글을 중복 수집했다"
    assert cursor is None, "중복만 오면 소진으로 봐야 한다"


@pytest.mark.asyncio
async def test_fetch_failure_keeps_cursor_for_retry(monkeypatch, tmp_path):
    """요청이 실패하면 커서를 남겨 다음 실행이 재시도해야 한다."""
    m = _load(monkeypatch, tmp_path, REDDIT_ARCTIC_MAX_PAGES=10)
    n = {"i": 0}

    async def fake_get(client, params):
        n["i"] += 1
        if n["i"] == 1:
            return [_item(_ts(2023, 8, 1), "a")]
        return None                            # 실패

    monkeypatch.setattr(m, "_get", fake_get)
    posts, cursor = await m._walk_sub(
        None, "samsung",
        datetime(2023, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert len(posts) == 1
    assert cursor is not None, "실패했는데 소진으로 처리했다 — 남은 구간을 잃는다"


# ── 상태 파일 ──────────────────────────────────────────────────────────
def test_state_roundtrip_is_atomic(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    m._save_state({"2023": {"samsung": "done"}})
    assert m._load_state() == {"2023": {"samsung": "done"}}
    # 임시파일이 남지 않아야 한다
    assert not list(tmp_path.glob("*.tmp"))


def test_state_missing_file_is_empty(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path,
              REDDIT_ARCTIC_STATE=str(tmp_path / "none.json"))
    assert m._load_state() == {}


def test_state_corrupt_file_is_empty(monkeypatch, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    m = _load(monkeypatch, tmp_path, REDDIT_ARCTIC_STATE=str(path))
    assert m._load_state() == {}, "깨진 상태 파일에 죽으면 백필이 영영 멈춘다"


# ── 창 계산 ────────────────────────────────────────────────────────────
def test_year_window_is_half_open(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    s, e = m._year_window(2023)
    assert s == datetime(2023, 1, 1, tzinfo=timezone.utc)
    assert e == datetime(2024, 1, 1, tzinfo=timezone.utc), "연말이 겹치거나 빈다"


def test_subs_default_matches_live_crawler(monkeypatch, tmp_path):
    """백필 대상이 실시간 수집 대상과 같아야 한다 — 갈라지면 한쪽만 역사가 생긴다."""
    monkeypatch.delenv("REDDIT_BACKFILL_SUBS", raising=False)
    m = _load(monkeypatch, tmp_path)
    from platforms.reddit_rss import SUBREDDITS
    assert m._subs() == list(SUBREDDITS)
