# 국가 추론이 '확실한 것만' 채우는지 검증한다 — 억지 추론은 지역 분석을 틀어뜨린다.
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from backfill_country_from_lang import AMBIGUOUS, LANG_TO_COUNTRY  # noqa: E402


def test_ambiguous_languages_are_excluded():
    """영어·스페인어·포르투갈어는 어느 나라인지 알 수 없다.

    억지로 정하면 지역별 결함 비교가 **조용히 틀어진다** — 없는 것보다 나쁘다.
    """
    for lang in ("en", "es", "pt", "ar", "de", "fr", "ru", "zh"):
        assert lang not in LANG_TO_COUNTRY, f"{lang} 는 국가를 특정할 수 없다"
        assert lang in AMBIGUOUS, f"{lang} 를 제외 사유와 함께 적어두지 않았다"


def test_latin_script_languages_are_excluded():
    """짧은 텍스트에서 라틴 문자 언어는 영어를 오탐한다.

    실측(2026-09-16) — da 로 잡힌 것이 "Still using fold 1"(영어),
    no 가 "Awesome video, killer intro."(영어), nl 이 "pixel is gem."(영어).
    비라틴 문자는 문자 자체가 증거라 안전하다.
    """
    for lang in ("no", "da", "sv", "nl", "ro", "pl", "tr", "id", "it", "fi", "cs", "hu"):
        assert lang not in LANG_TO_COUNTRY, \
            f"{lang} 는 라틴 문자라 짧은 글에서 영어를 오탐한다"


def test_mapped_languages_are_one_to_one():
    """매핑된 언어는 사실상 한 나라에서만 쓰여야 한다."""
    expected = {
        "ko": "KR", "ja": "JP", "th": "TH", "vi": "VN",
        "el": "GR", "he": "IL", "fa": "IR", "uk": "UA",
        "bg": "BG", "hi": "IN", "ta": "IN", "bn": "BD",
    }
    assert LANG_TO_COUNTRY == expected, LANG_TO_COUNTRY


def test_country_codes_are_iso_two_letter():
    for lang, code in LANG_TO_COUNTRY.items():
        assert len(code) == 2 and code.isupper(), f"{lang} → {code!r}"


def test_ambiguous_reasons_are_written():
    """왜 제외했는지 적어야 다음 사람이 재검토할 수 있다."""
    for lang, why in AMBIGUOUS.items():
        assert len(why) >= 6, f"{lang}: 사유가 부실하다 — {why!r}"


def test_update_only_touches_null_rows():
    """이미 국가가 있는 행을 덮으면 크롤러가 준 정확한 값을 잃는다."""
    src = (ROOT / "scripts" / "backfill_country_from_lang.py").read_text()
    upd = src.split("UPDATE_SQL", 1)[1].split('""")', 1)[0]
    assert "country_code IS NULL" in upd, "NULL 이 아닌 행도 덮어쓴다"
