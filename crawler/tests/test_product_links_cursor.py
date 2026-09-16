# 제품 링크 백필이 커서를 이어받는지 검증한다 — 없으면 앞부분만 영원히 반복한다.
"""이 테스트가 막는 고장.

backfill_product_links 는 하루치를 LINK_LIMIT 으로 끊어 돈다. 그런데 커서가
없으면 매 실행 after=0 으로 되돌아가, 대상 524,714건 중 **앞 60,000건만** 매일
다시 훑고 나머지 464,714건에는 영영 닿지 않는다(2026-09-16 발견).

실패 신호가 전혀 없다는 게 고약하다 — 로그는 매일 "스캔 60000, 링크 30156"으로
성공처럼 찍힌다. 창을 고정하면 꼬리만 긁는다.
"""
import importlib
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("LINK_STATE", str(tmp_path / "state.json"))
    import backfill_product_links as m
    return importlib.reload(m)


def test_cursor_roundtrip(mod):
    assert mod.read_cursor() == 0, "첫 실행은 0 에서 시작해야 한다"
    mod.write_cursor(12345, False)
    assert mod.read_cursor() == 12345, "다음 실행이 이어받지 못한다"


def test_missing_state_starts_at_zero(mod):
    assert not mod.STATE_PATH.exists()
    assert mod.read_cursor() == 0


def test_corrupt_state_starts_at_zero(mod):
    """커서 파일이 깨졌다고 죽으면 안 된다 — 백필은 멱등이라 처음부터 가도 손해가 없다."""
    mod.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    mod.STATE_PATH.write_text("{ 이건 JSON 이 아니다")
    assert mod.read_cursor() == 0


def test_state_records_wrap(mod):
    mod.write_cursor(0, True)
    assert json.loads(mod.STATE_PATH.read_text())["wrapped"] is True


def test_cursor_is_saved_even_on_interrupt(mod):
    """중간에 끊겨도 진도가 남아야 한다 — finally 에서 써야 하는 이유다."""
    src = (ROOT / "scripts" / "backfill_product_links.py").read_text()
    tail = src.split("    finally:", 1)[1].split("log.info", 1)[0]
    assert "write_cursor" in tail, "커서를 finally 에 저장하지 않는다"


def test_wrap_only_happens_once_per_run(mod):
    """되감기를 회차마다 무제한 허용하면 LINK_LIMIT 이 없을 때 무한히 돈다."""
    src = (ROOT / "scripts" / "backfill_product_links.py").read_text()
    guard = src.split("if not rows:", 1)[1].split("continue", 1)[0]
    assert "wrapped or" in guard, "되감기를 한 번으로 제한하지 않는다"
    assert "not LIMIT" in guard, "상한이 없는 회차에서도 되감아 무한 루프가 된다"


def test_select_uses_keyset_not_offset(mod):
    """OFFSET 은 뒤로 갈수록 느려진다 — 50만 행에서는 keyset 이어야 한다."""
    src = (ROOT / "scripts" / "backfill_product_links.py").read_text()
    sel = src.split("SELECT_SQL", 1)[1].split('""")', 1)[0]
    assert "id > :after" in sel
    assert "OFFSET" not in sel.upper()
