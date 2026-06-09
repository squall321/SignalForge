"""test_relink — 옛 디바이스 매핑 사전 단위 테스트.

Track D — relink_products.py 의 match_product_code 정확성 검증.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.relink_products import match_product_code, MODEL_MAP  # noqa: E402


def test_note7_matches_GN7():
    """'Note 7' / '갤럭시 노트 7' / 'note7' 모두 GN7."""
    assert match_product_code("My old Note 7 still works") == "GN7"
    assert match_product_code("갤럭시 노트 7 발화 사건") == "GN7"
    assert match_product_code("note7 battery") == "GN7"


def test_s10_5g_matches_GS105G():
    """'Galaxy S10 5G' / 'S10 5G' 매칭 — S10 일반보다 우선."""
    assert match_product_code("Galaxy S10 5G was first 5G phone") == "GS105G"
    assert match_product_code("s10 5g 사용기") == "GS105G"
    # S10 단독은 GS10 으로 매칭되어야 함 (5G 가 없으면)
    assert match_product_code("Galaxy S10 review") == "GS10"


def test_no_match_returns_none():
    """매칭 키 없는 텍스트는 None."""
    assert match_product_code("Just a generic comment about phones") is None
    assert match_product_code("") is None
    assert match_product_code("배터리 광탈") is None


def test_note20_ultra_priority_over_note20():
    """'note 20 ultra' 가 'note 20' 보다 먼저 매칭 (길이 내림차순 정렬)."""
    assert match_product_code("Galaxy Note 20 Ultra is huge") == "GN20U"
    assert match_product_code("note 20 ultra 카메라") == "GN20U"
    # 'note 20' 단독은 GN20
    assert match_product_code("note 20 일반형") == "GN20"


def test_fold_flip_legacy():
    """Z Fold/Flip 1~4 매칭."""
    assert match_product_code("Galaxy Fold first foldable") == "GZF1"
    assert match_product_code("z fold 3 hinge issue") == "GZF3"
    assert match_product_code("폴드4 사용 후기") == "GZF4"
    assert match_product_code("z flip 3 화면") == "GZFL3"


def test_dict_size_at_least_30():
    """사전이 30개 이상 변이를 포함."""
    assert len(MODEL_MAP) >= 30


# === R7 트랙 A: 카탈로그 보유 코드 보강 테스트 (2026-06-04) ===


def test_s22_s26_variants():
    """Galaxy S22~S26 (Ultra/+/FE 포함) 매핑."""
    assert match_product_code("Galaxy S22 Ultra 카메라 최고") == "GS22U"
    assert match_product_code("갤럭시 s22") == "GS22"
    assert match_product_code("Galaxy S23 Ultra 220g") == "GS23U"
    assert match_product_code("s23+ review") == "GS23P"
    assert match_product_code("Galaxy S23 FE 출시일") == "GFE23"
    assert match_product_code("갤럭시 s24") == "GS24"
    assert match_product_code("Galaxy S24 Ultra 티타늄") == "GS24U"
    assert match_product_code("Galaxy S25 Ultra 1000nit") == "GS25U"
    assert match_product_code("galaxy s25+") == "GS25P"
    assert match_product_code("Galaxy S26 출시 임박") == "GS26"
    assert match_product_code("s26 ultra leaks") == "GS26U"


def test_fold_flip_5_to_8():
    """Z Fold/Flip 5~8 매핑."""
    assert match_product_code("Galaxy Z Fold5 무게") == "GZF5"
    assert match_product_code("z fold6 hinge") == "GZF6"
    assert match_product_code("폴드7 사용 후기") == "GZF7"
    assert match_product_code("Galaxy Z Fold8 루머") == "GZF8"
    assert match_product_code("Z Flip5 커버 디스플레이") == "GZFL5"
    assert match_product_code("플립6 색상") == "GZFL6"
    assert match_product_code("Galaxy Z Flip7") == "GZFL7"


def test_watch_6_to_ultra():
    """Watch 6/7/8/Ultra 매핑.

    R8 (0011) 부터 'Watch6 Classic' 은 별도 코드 GW6C 로 분리 매칭. 일반
    Watch6 (Classic 미포함) 은 GW6 그대로.
    """
    assert match_product_code("Galaxy Watch6 Classic 사용기") == "GW6C"
    assert match_product_code("Galaxy Watch6 batteries") == "GW6"
    assert match_product_code("갤럭시 워치7 배터리") == "GW7"
    assert match_product_code("Galaxy Watch Ultra 티타늄 case") == "GWU"


def test_buds_2_to_4():
    """Buds2/3/4 (Pro 포함) 매핑 — Galaxy 컨텍스트 필요."""
    assert match_product_code("Galaxy Buds2 Pro 노이즈 캔슬링") == "GB2P"
    assert match_product_code("갤럭시 버즈3") == "GB3"
    assert match_product_code("Galaxy Buds4 Pro 출시") == "GB4P"


def test_iphone_extended():
    """iPhone 6/7/8/X/14/15/16 매핑."""
    assert match_product_code("iPhone X 가격") == "AP10"
    assert match_product_code("iphone 8 plus battery") == "AP8"
    assert match_product_code("아이폰 16 프로 맥스 무게") == "AP16PM"
    assert match_product_code("iPhone 15 Pro 티타늄") == "AP15P"


def test_pixel_extended():
    """Pixel 2/3/4/8/9 매핑."""
    assert match_product_code("Pixel 9 Pro 카메라") == "PX9P"
    assert match_product_code("Google Pixel 8 Tensor G3") == "PX8"
    assert match_product_code("픽셀 4 XL") == "PX4"


# === R7 트랙 B: 모호 키 + 컨텍스트 가드 테스트 ===


def test_yc_noise_filtered():
    """YC batch 표기는 매칭에서 제외 (Y Combinator)."""
    # 'YC S22' 만 등장 — Galaxy 컨텍스트 없으면 GS22 매칭 금지
    assert match_product_code("Acme is a YC S22 startup") is None
    assert match_product_code("YC W21 batch alumni") is None
    # 'YC S22' + 'Galaxy S22' 동시 등장 — Galaxy 컨텍스트가 있으므로 매칭 허용
    assert match_product_code("Founded YC S22, our app supports Galaxy S22") == "GS22"


def test_notebook_noise_filtered():
    """'notebook' 은 Galaxy Note 와 무관."""
    assert match_product_code("My notebook is slow") is None


def test_app_store_note_filtered():
    """'app store note' 결합어는 Galaxy Note 매칭 거부."""
    # 키 'note' 단독은 사전에 없지만 'app store note' 마스킹 동작 확인용
    # 'note 7' 단독은 사전에 있으므로 다른 단어 결합 노이즈에 주의
    text = "App Store note about the new Galaxy Note 7 update"
    # 'galaxy note 7' 이 우선 매칭 — 마스킹은 그 후
    assert match_product_code(text) == "GN7"


def test_dict_size_grew_after_r7():
    """R7 사전 확장 후 키 개수 200 이상."""
    assert len(MODEL_MAP) >= 200
