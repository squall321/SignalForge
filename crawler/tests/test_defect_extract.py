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


# ── 재현율 표본(200건)에서 드러난 위음성군 — 실제 놓쳤던 문장 ────────────
@pytest.mark.parametrize("text,expected", [
    # 증상 패턴이 부정어를 품어 자기 자신을 지우던 버그
    ("not working", ("device", "not_working")),
    ("the phone is not turning on", ("device", "no_power")),
    ("Galaxy Tab S9 not charging but recognising power", ("charging_port", "no_charge")),
    # 어휘 굴절 — swollen (safety 급 결함이 통째로 누락됐다)
    ("the rear battery was swollen", ("battery", "swelling")),
    # 부정+동사 조합
    ("home up not opening in 8.5 one ui", ("software", "not_working")),
    ("the watch turned off but didn't turn back on", ("device", "no_power")),
    ("your Galaxy Watch may not turn back on automatically", ("device", "no_power")),
    ("the UI update won't update", ("software", "update_fail")),
    ("Samsung smart switch update stuck on 0%", ("software", "update_fail")),
    # 완곡·구어 표현
    ("The battery runs out fast", ("battery", "drain")),
    ("the battery doesn't last a day", ("battery", "drain")),
    ("my phone suddenly wasn't holding a charge", ("battery", "drain")),
    ("my phone was noticeably hot the moment I opened it", ("thermal", "overheat")),
    ("Fold 8 heats up really bad", ("thermal", "overheat")),
    # 색상 변형 — green 만 있고 pink/magenta 는 없었다
    ("Magenta/Pink Line on my Samsung Flip 5", ("device", "green_line")),
    ("HELP! Line on Screen. Fixes ??", ("display", "green_line")),
    # 증상 클래스 자체가 없던 것
    ("it came with some bubbling on the interior screen protector", ("display", "bubble")),
    ("the film on my 6th fold began to bubble", ("display", "bubble")),
    # 한국어 변형
    ("전원이 켜지지 않아요", ("device", "no_power")),
    ("앱이 안 열려요", ("device", "not_working")),
    ("업데이트가 안 됩니다", ("software", "not_working")),
    ("배터리 광탈이에요", ("battery", "drain")),
    ("화면에 분홍 줄이 생겼어요", ("display", "green_line")),
    ("필름에 기포가 생겼어요", ("display", "bubble")),
])
def test_recall_gaps_now_detected(text, expected):
    assert expected in pairs(text), f"재현율 표본에서 확인된 결함을 놓침: {text}"


# ── 재현율 보강과 함께 막은 오탐 (표본·코퍼스 실측) ──────────────────────
@pytest.mark.parametrize("text", [
    # 은유적 broke/broken — 표본 오탐 3건의 원인
    "we were apparently almost broke",
    "a scrub bar broken into melody / chime / ambience",
    "Sue Storm describing him as a broken person",
    "blogger @digitalchatstation broke the news today",
    # 'dead' 가 제목·인명에서 전원사망으로 잡히던 것
    "Pixel brawler The Walking Dead: Streets of Survival arrives September 18",
    # 날씨·경기 얘기가 발열로
    "The weather is really hot these days. I hope everyone is taking good care",
    "Samsung's memory business is at a very hot point",
    "The wireless headset that gets hot-swappable batteries right",
    # 'signifi|cant upgrade' 가 업데이트 실패로 (선행 \b 누락)
    "a significant upgrade is expected on the performance side of the device",
    # 사람이 근무 중이 아님 / 출시 계획 / 사람 무반응
    "I use my phone when I'm not working",
    "Samsung will not launch the Galaxy Watch9 Classic version this year",
    "emergency detection to call for help if the user becomes unresponsive",
    # 'turn up'(나타나다)
    "it gets you an independent check that they were aware they didn't turn up",
    # 설정 토글 — 전원 사망이 아니다
    "I was so irritated that I didn't turn on the vibrating sound",
    # bubble 오탐군
    "new ways to multitask with Bubbles, Screen Reactions for creators",
    "simple bubble level, magnifying glass",
    "Visually, the display looks perfect: no bubbles",
    # 'aswell' 오타가 swelling 으로 (\b 누락)
    "the left one is starting to flake on the charging aswell",
])
def test_recall_patch_false_positives_suppressed(text):
    assert extract_defects(text) == [], f"오탐이 되살아남: {text}"


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
