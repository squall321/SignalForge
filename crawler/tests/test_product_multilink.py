# 다대다 제품 추출(infer_all_product_codes) 단위 테스트 — span 겹침 억제·역할 판정·primary 정합
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.product_match import (  # noqa: E402
    infer_all_product_codes,
    infer_product_code,
)


def codes(text):
    return [c for c, _ in infer_all_product_codes(text)]


def roles(text):
    return dict(infer_all_product_codes(text))


# ── span 겹침 억제 — 이 함수의 핵심 위험 ──────────────────────────────
@pytest.mark.parametrize("text,expected", [
    # 'Galaxy S26 Ultra' 는 GS26U(7,16) 와 GS26(0,10) 을 동시 매칭 → GS26 억제
    ("Galaxy S26 Ultra camera issue", ["GS26U"]),
    ("갤럭시 S26 울트라 배터리", ["GS26U"]),
    ("Galaxy Z Fold8 hinge dust", ["GZF8"]),
    ("Galaxy S25 Ultra 화면", ["GS25U"]),
])
def test_overlap_suppressed(text, expected):
    assert codes(text) == expected


# ── 겹치지 않는 다중 제품은 모두 보존 (비교글 신호) ────────────────────
def test_comparison_keeps_both():
    got = codes("S26 Ultra vs Fold8 어느 게 나은가")
    assert "GS26U" in got and "GZF8" in got


def test_three_products():
    got = codes("Galaxy S26 Ultra, Galaxy Z Fold8, iPhone 16 Pro 비교")
    assert {"GS26U", "GZF8", "AP16P"} <= set(got)


# ── 역할 판정 ────────────────────────────────────────────────────────
def test_primary_is_first():
    out = infer_all_product_codes("Galaxy S26 Ultra vs Galaxy Z Fold8")
    assert out[0][1] == "primary"
    assert sum(1 for _, r in out if r == "primary") == 1


def test_compared_role_when_marker():
    r = roles("Galaxy S26 Ultra vs Galaxy Z Fold8")
    assert r["GZF8"] == "compared"


def test_mentioned_role_without_marker():
    r = roles("Galaxy S26 Ultra 쓰다가 Galaxy Z Fold8 샀다")
    assert r["GZF8"] == "mentioned"


# ── primary 는 기존 infer_product_code 와 항상 일치해야 함 (product_id 정합) ──
@pytest.mark.parametrize("text", [
    "Galaxy S26 Ultra vs Fold8",
    "갤럭시 Z 폴드8 힌지에 먼지",
    "iPhone 16 Pro overheating after update",
    "갤럭시 워치9 페어링 안 됨",
    "Galaxy A57 카메라",
    "아무 제품도 없는 잡담",
])
def test_primary_matches_single_infer(text):
    out = infer_all_product_codes(text)
    single = infer_product_code(text)
    if single is None:
        assert out == []
    else:
        assert out[0][0] == single
        assert out[0][1] == "primary"


# ── 회귀 — 매치 없음/빈 입력 ─────────────────────────────────────────
@pytest.mark.parametrize("text", ["", None, "그냥 일반 잡담입니다", "Galaxy Watch"])
def test_no_match(text):
    assert infer_all_product_codes(text) == []


# ── 중복 코드가 나오지 않아야 함 (링크 테이블 PK 충돌 방지) ───────────
def test_no_duplicate_codes():
    got = codes("Galaxy S26 Ultra 좋다. S26 Ultra 정말 좋다. Fold8 도 괜찮다.")
    assert len(got) == len(set(got))
