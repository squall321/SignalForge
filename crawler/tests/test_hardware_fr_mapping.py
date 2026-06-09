"""Harvest 5 V3 — Hardware.fr (French forum) MODEL_MAP 보강 검증.

목표: Hardware.fr 의 프랑스어 포럼 thread title 형식
(예: 'Samsung Galaxy S20/S20+/S20 Ultra & S20 FE [T.U.]',
'Samsung J3 de 2016 - Téléphone Android') 매칭률 개선.

추가된 사전 (regex):
- Galaxy A3~A9 (단일 자릿수, 옛 모델 2017~2018)
- Galaxy/Samsung J1~J8 (J 시리즈 — Samsung 컨텍스트 인정)
- A9 Pro / J7 Prime / J7 Pro / J7 Max / J5 Prime / J2 Pro / J1 mini 변형
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.relink_products import match_product_code  # noqa: E402


def test_hardware_fr_french_titles_samsung_old_a():
    """프랑스어 포럼 thread title — 옛 A 시리즈."""
    cases = [
        ("Galaxy A6 Pas de réception des SMS", "GA6_18"),
        ("Galaxy A8 problème batterie", "GA8_18"),
        ("Galaxy A5 2017 mise à jour", "GA5_17"),
        ("Galaxy A7 (2018) avis", "GA7_18"),
        ("Galaxy A3 nouveau", "GA3_17"),
        ("Galaxy A9 sortie", "GA9_18"),
        ("galaxy a9 pro fiche technique", "GA9P_16"),
    ]
    for text, expected in cases:
        assert match_product_code(text) == expected, f"failed: {text!r}"


def test_hardware_fr_j_series_samsung_or_galaxy_context():
    """J 시리즈 — Samsung 또는 Galaxy 컨텍스트 필수."""
    cases = [
        ("Samsung J3 de 2016 - Téléphone Android", "GJ3_16"),
        ("galaxy j7 prime test", "GJ7PRM"),
        ("samsung j7 max review", "GJ7MAX"),
        ("Samsung J5 prime test", "GJ5PRM"),
        ("galaxy j7 pro user manual", "GJ7PRO"),
        ("Samsung J5 2016 forum", "GJ5"),
        ("galaxy j8 dual sim", "GJ8"),
        ("samsung j1 mini specs", "GJ1M"),
        ("samsung j2 pro features", "GJ2PRO"),
    ]
    for text, expected in cases:
        assert match_product_code(text) == expected, f"failed: {text!r}"


def test_no_collision_two_digit_a_series():
    """word boundary 로 'Galaxy A60' / 'Galaxy A50' 등 두 자릿수 미오인."""
    # GA60 / GA50 / GA70 / GA80 등은 별도 카탈로그.
    # 우리 추가 패턴은 단일 자릿수 (A3, A5, A6, ...) 만 잡아야 한다.
    cases = [
        ("Galaxy A60 review", "GA60"),  # 기존 패턴 우선
        ("Galaxy A50 forum", "GA50"),
        ("Galaxy A70 thread", "GA70"),
        ("Galaxy A80 specs", "GA80"),
        ("Galaxy A52 problem", "GA52"),
    ]
    for text, expected in cases:
        assert match_product_code(text) == expected, f"failed: {text!r}"


def test_existing_french_titles_still_match():
    """기존 매칭 회귀 방지 — Hardware.fr S/Note/Z 시리즈."""
    cases = [
        ("Samsung Galaxy S20/S20+/S20 Ultra & S20 FE [T.U.]", "GS20"),
        ("Samsung Galaxy S10e/ S10/ S10+ [Topic Unique]", "GS10E"),
        ("Samsung galaxy S9/S9+ [Topic unique]", "GS9"),
        ("Samsung Galaxy S21/S21+/S21 Ultra & S21 FE [T.U.]", "GS21"),
    ]
    for text, expected in cases:
        assert match_product_code(text) == expected, f"failed: {text!r}"


def test_non_samsung_titles_remain_unmapped():
    """비-Samsung 모델은 unmapped 정책 유지."""
    cases = [
        "Xiaomi 17 & 17 Ultra [T.U.]",
        "Honor Magic V + (Tous les Magic V) [T.U.]",
        "Poco F8 Pro et F8 Ultra",
        "Huawei P30 Lite/ P30 /P30 Pro [topic unique]",
        "Sony Xperia 2026, Xperia 1 VIII en fuite ! [Topic Unique]",
        "Motorola signature - Téléphone Android",
        "Blackview [Topic unique]",
    ]
    for text in cases:
        assert match_product_code(text) is None, f"should be None: {text!r}"


def test_bare_j_without_context_not_matched():
    """J 단독 (Samsung/Galaxy 컨텍스트 없음) 은 매칭 금지 — 노이즈 차단."""
    cases = [
        "J7 cores in ARM Cortex",
        "J5 jumper wire",
        "J3 connector pinout",
    ]
    for text in cases:
        # 정책: Samsung/Galaxy 키워드 없이 J숫자 단독은 안 잡음.
        # (현재 패턴은 (?:galaxy|samsung)\s+j\s*N 필수)
        result = match_product_code(text)
        assert result is None, f"should be None: {text!r}, got {result}"
