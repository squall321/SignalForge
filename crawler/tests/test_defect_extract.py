# 결함 구조화 추출(extract_defects) 단위 테스트 — 근접 페어링·심각도·한영 혼용
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nlp.defect_extract import extract_defects  # noqa: E402


def pairs(text):
    return {(c, s) for c, s, _ in extract_defects(text)}


def sev(text, component, symptom):
    for c, s, v in extract_defects(text):
        if (c, s) == (component, symptom):
            return v
    return None


# ── 근접 페어링 — 이 추출기의 핵심 위험(교차곱 노이즈) ────────────────
def test_pairs_symptom_with_nearest_component():
    # 힌지-먼지 / 카메라-흐림 이 서로 섞이면 안 된다
    t = "The hinge collected dust after a month. Also the camera is blurry in low light."
    p = pairs(t)
    assert ("hinge", "dust_ingress") in p
    assert ("camera", "blurry") in p
    assert ("camera", "dust_ingress") not in p
    assert ("hinge", "blurry") not in p


def test_korean_hinge_dust():
    p = pairs("폴드8 힌지 틈으로 먼지가 들어갑니다")
    assert ("hinge", "dust_ingress") in p


def test_far_component_not_paired():
    # 부품이 window 밖이면 증상이 함의하는 기본 부품으로 귀속
    t = "hinge" + " " * 300 + "the battery drains fast"
    p = pairs(t)
    assert ("battery", "drain") in p
    assert ("hinge", "drain") not in p


# ── 부품 언급 없는 증상 → 함의 부품 / device ──────────────────────────
def test_implied_component():
    assert ("thermal", "overheat") in pairs("It overheats constantly")
    assert ("software", "boot_loop") in pairs("stuck in a bootloop")


def test_device_fallback():
    p = pairs("침수됐어요")
    assert ("device", "water_damage") in p


# ── 심각도 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,comp,symp,expected", [
    ("the battery started swelling", "battery", "swelling", "safety"),
    ("phone won't turn on at all", "device", "no_power", "non_functional"),
    ("screen flickers sometimes", "display", "flicker", "degraded"),
    ("there is a scratch on the frame", "frame", "scratch", "cosmetic"),
])
def test_severity(text, comp, symp, expected):
    assert sev(text, comp, symp) == expected


def test_safety_outranks_in_taxonomy():
    got = extract_defects("battery swelling and the case has a scratch")
    sevs = {s: v for _, s, v in got}
    assert sevs["swelling"] == "safety"
    assert sevs["scratch"] == "cosmetic"


# ── 실제 코퍼스에서 관측된 폴드8 결함 문구 ───────────────────────────
def test_real_fold8_phrases():
    p = pairs("Unsatisfied with the fold8 build quality, there is a gap in the hinge "
              "and a green line appeared on the screen")
    assert ("hinge", "gap") in p
    assert ("display", "green_line") in p


# ── 회귀 — 빈 입력·무관 텍스트 ───────────────────────────────────────
@pytest.mark.parametrize("text", ["", None, "오늘 날씨가 좋네요", "I love this phone"])
def test_no_defect(text):
    assert extract_defects(text) == []


def test_dedup_and_cap():
    got = extract_defects("crack crack crack " * 30)
    assert len(got) == len(set(got))
    assert len(got) <= 12


# ── 부정·반증 회귀 (적대적 검증에서 확인된 실제 오탐) ─────────────────
@pytest.mark.parametrize("text", [
    # 'hang' 이 changing 안에서 매칭되던 것 (선행 \b 누락)
    "Apple is said to be changing its OLED display panel procurement method",
    # 'battery life' 는 중립·마케팅 문구 — drain 리터럴에서 제거
    "tuned specifically for all-day battery life",
    "nor is a battery life of only 36-48 hours a problem",
    # 부정·반증 표현
    "protected by Gorilla Glass Ceramic 3, scratch-resistant",
    "Absolute mint condition Not a scratch or Mark",
    "AirPlay 2 speakers work more stable and with less lag",
    # 관용구가 non_functional 로 승격되던 것
    "The battery is dead simple to replace",
])
def test_known_false_positives_suppressed(text):
    assert extract_defects(text) == []


@pytest.mark.parametrize("text,expected", [
    ("the battery drains fast", ("battery", "drain")),
    ("battery dies within 3 hours", ("battery", "drain")),
    ("my phone freezes constantly", ("software", "freeze")),
    ("the app hangs when I open it", ("software", "freeze")),
    ("the screen has a scratch on it", ("display", "scratch")),
    ("phone is dead, won't turn on", ("device", "no_power")),
])
def test_true_positives_still_detected(text, expected):
    assert expected in pairs(text), f"진짜 결함을 놓침: {text}"


# ── 증상 정밀도 회귀 (라벨 표본에서 firsthand 0% 로 드러난 오탐군) ─────
@pytest.mark.parametrize("text", [
    # dust — 방진 스펙·방치 관용구
    "IP68 dust and water resistance rating",
    "accessories collecting dust in my drawer",
    "my phone has been sitting collecting dust",
    # gap — 기간·가격 격차
    "there is a five-year gap between releases",
    "the price gap between the two models is huge",
    # crease — 폴더블 리뷰의 중립·긍정 서술
    "the crease is barely visible unless in direct sunlight",
    "주름이 직사광선 아니면 거의 안 보인다",
    # 매체 분해 리뷰·액세서리 안내 (실제 알림 근거였던 문장들)
    "iFixit scores Samsung Galaxy Z Fold8 4/10 for repairability",
    "Galaxy Z Fold 8 durability put to test, and it passes with flying colors",
    "Advantages of Fold 8 hinge protection case",
])
def test_symptom_precision_false_positives(text):
    assert extract_defects(text) == []


@pytest.mark.parametrize("text,expected", [
    ("dust got into the hinge after a month", ("hinge", "dust_ingress")),
    ("The hinge collected dust after a month", ("hinge", "dust_ingress")),
    ("힌지에 먼지가 들어갔어요", ("hinge", "dust_ingress")),
    ("there is a visible gap in the hinge when closed", ("hinge", "gap")),
    ("힌지 유격이 생겼습니다", ("hinge", "gap")),
    ("the crease got worse and is now really noticeable", ("display", "crease")),
    ("주름이 점점 심해집니다", ("display", "crease")),
    # 조동사 부정형은 부정이 아니라 결함 표현 자체다
    ("Galaxy Z Fold8's hinge won't open due to powder trapped inside",
     ("hinge", "dust_ingress")),
])
def test_symptom_precision_true_positives(text, expected):
    assert expected in pairs(text), f"진짜 결함을 놓침: {text}"
