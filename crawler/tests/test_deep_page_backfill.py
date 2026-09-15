# 깊이 백필의 페이지 커서 전진·바닥 판정·실패 보존을 검증한다.
import asyncio
import importlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def _load(monkeypatch, tmp_path, **env):
    env.setdefault("DEEP_STATE", str(tmp_path / "state.json"))
    env.setdefault("DEEP_PAGES_PER_RUN", "10")
    env.setdefault("DEEP_SITES", "dcinside")
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    import deep_page_backfill as m
    return importlib.reload(m)


def _stub_runs(m, monkeypatch, saved_seq):
    """_run_site 를 순서대로 값을 돌려주는 가짜로 바꾸고 호출된 시작 페이지를 모은다."""
    calls = []
    seq = list(saved_seq)

    async def fake(site, start_page):
        calls.append(start_page)
        return seq.pop(0) if seq else 0

    monkeypatch.setattr(m, "_run_site", fake)
    return calls


def test_cursor_advances_each_run(monkeypatch, tmp_path):
    """매 실행 다음 구간으로 내려가야 한다 — 이게 없으면 제자리걸음이다."""
    m = _load(monkeypatch, tmp_path)
    calls = _stub_runs(m, monkeypatch, [5, 5, 5])
    for _ in range(3):
        asyncio.run(m.main())
    assert calls == [1, 11, 21], f"커서가 전진하지 않았다: {calls}"


def test_windows_do_not_skip_pages(monkeypatch, tmp_path):
    """연속 실행의 창이 빈틈 없이 이어져야 한다 — 빈틈은 영구 누락이다."""
    m = _load(monkeypatch, tmp_path, DEEP_PAGES_PER_RUN=7)
    calls = _stub_runs(m, monkeypatch, [1, 1, 1])
    for _ in range(3):
        asyncio.run(m.main())
    covered = []
    for start in calls:
        covered.extend(range(start, start + 7))
    assert covered == list(range(1, 22)), f"창에 빈틈/겹침: {covered}"


def test_empty_rounds_reset_to_floor(monkeypatch, tmp_path):
    """연속 0건이면 바닥으로 보고 처음부터 다시 순회한다."""
    m = _load(monkeypatch, tmp_path, DEEP_EMPTY_LIMIT=2)
    calls = _stub_runs(m, monkeypatch, [0, 0, 3])
    for _ in range(3):
        asyncio.run(m.main())
    # 1 → (0건) 11 → (0건, 한도 도달) 1 로 되돌림
    assert calls == [1, 11, 1], f"바닥 판정이 동작하지 않았다: {calls}"


def test_nonzero_save_resets_empty_counter(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, DEEP_EMPTY_LIMIT=2)
    _stub_runs(m, monkeypatch, [0, 4, 0])
    for _ in range(3):
        asyncio.run(m.main())
    st = m._load_state()["dcinside"]
    assert st["empty_rounds"] == 1, "저장이 있었는데 0건 카운터가 안 풀렸다"


def test_failure_keeps_cursor(monkeypatch, tmp_path):
    """실행이 터지면 커서를 옮기지 않아야 한다 — 옮기면 그 구간을 영영 건너뛴다."""
    m = _load(monkeypatch, tmp_path)
    calls = []

    async def boom(site, start_page):
        calls.append(start_page)
        raise RuntimeError("network down")

    monkeypatch.setattr(m, "_run_site", boom)
    asyncio.run(m.main())
    asyncio.run(m.main())
    assert calls == [1, 1], f"실패했는데 커서를 옮겼다: {calls}"


def test_dry_run_does_not_move_cursor(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, DRY_RUN=1)
    calls = _stub_runs(m, monkeypatch, [0, 0])
    asyncio.run(m.main())
    asyncio.run(m.main())
    assert calls == [1, 1], "DRY_RUN 이 상태를 건드렸다"


def test_state_survives_corruption(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{broken")
    m = _load(monkeypatch, tmp_path, DEEP_STATE=str(bad))
    assert m._load_state() == {}, "깨진 상태에 죽으면 백필이 영영 멈춘다"


def test_clien_floor_is_zero(monkeypatch, tmp_path):
    """clien 은 0-indexed — 바닥이 1 이면 첫 페이지를 영영 안 본다."""
    m = _load(monkeypatch, tmp_path, DEEP_SITES="clien")
    calls = _stub_runs(m, monkeypatch, [2])
    asyncio.run(m.main())
    assert calls == [0], f"clien 시작 페이지가 {calls}"


def test_selected_sites_are_known_only(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, DEEP_SITES="dcinside,없는사이트")
    assert m._selected() == ["dcinside"]
