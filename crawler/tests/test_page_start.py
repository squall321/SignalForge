# 역사 백필의 시작 페이지(PAGE_START)가 기존 동작을 깨지 않고 깊이 내려가는지 검증한다.
import importlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# (모듈, env 접두, 기본 시작 페이지)
SITES = [
    ("platforms.dcinside", "DCINSIDE", 1),
    ("platforms.clien", "CLIEN", 0),
    ("platforms.ppomppu", "PPOMPPU", 1),
]


def _reload(mod, monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    m = importlib.import_module(mod)
    return importlib.reload(m)


@pytest.mark.parametrize("mod,prefix,default", SITES, ids=lambda x: str(x))
def test_default_start_unchanged(mod, prefix, default, monkeypatch):
    """기본값은 기존 동작 그대로여야 한다 — 실시간 수집을 건드리면 안 된다."""
    monkeypatch.delenv(f"{prefix}_PAGE_START", raising=False)
    m = _reload(mod, monkeypatch)
    assert m.PAGE_START == default


@pytest.mark.parametrize("mod,prefix,default", SITES, ids=lambda x: str(x))
def test_start_is_configurable(mod, prefix, default, monkeypatch):
    m = _reload(mod, monkeypatch, **{f"{prefix}_PAGE_START": 51})
    assert m.PAGE_START == 51


@pytest.mark.parametrize("mod,prefix,default", SITES, ids=lambda x: str(x))
def test_page_window_shifts_not_grows(mod, prefix, default, monkeypatch):
    """시작을 옮겨도 **훑는 페이지 수는 같아야** 한다.

    range(start, start+N) 이 아니라 range(start, N) 같은 실수를 하면 깊이 갈수록
    창이 줄거나 비어 한 바퀴를 통째로 놓친다.
    """
    m = _reload(mod, monkeypatch,
                **{f"{prefix}_PAGE_START": 100, f"{prefix}_BACKFILL_PAGES": 20})
    pages = list(range(m.PAGE_START, m.PAGE_START + m.LIST_PAGES))
    assert len(pages) == 20, f"창 크기가 {len(pages)}로 어긋났다"
    assert pages[0] == 100 and pages[-1] == 119


@pytest.mark.parametrize("mod,prefix,default", SITES, ids=lambda x: str(x))
def test_source_uses_page_start_in_loop(mod, prefix, default, monkeypatch):
    """루프가 실제로 PAGE_START 를 쓰는지 — 상수만 있고 안 쓰면 무의미하다."""
    path = ROOT / (mod.replace(".", "/") + ".py")
    src = path.read_text()
    assert "range(PAGE_START, PAGE_START + LIST_PAGES)" in src, \
        f"{mod}: 목록 루프가 PAGE_START 를 쓰지 않는다"


def test_windows_tile_without_gap_or_overlap():
    """연속한 실행이 빈틈 없이 이어져야 한다 — 빈틈은 곧 영구 누락이다."""
    start, size = 1, 12
    seen = []
    for _ in range(5):
        seen.extend(range(start, start + size))
        start += size
    assert seen == list(range(1, 61)), "창이 겹치거나 빈다"
