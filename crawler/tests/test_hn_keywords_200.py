"""
HN R12 — 검색어 200+ 확장 검증

목표:
- platforms.hackernews.QUERY_TERMS 가 최소 200 개 이상이고
  9 그룹 대표 키워드 + 다국어 + 위기 키워드가 모두 포함되어야 한다.
- _load_query_terms() 가 HN_TERMS_FILE 환경변수를 우선 사용, 미지정 시 QUERY_TERMS 를 그대로 반환.
- scripts.hn_backfill_alltime.DEFAULT_TERMS 가 QUERY_TERMS 를 superset 으로 포함.

실행:
  cd crawler && python -m pytest tests/test_hn_keywords_200.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms.hackernews import QUERY_TERMS, _load_query_terms  # noqa: E402
from scripts.hn_backfill_alltime import DEFAULT_TERMS as BACKFILL_TERMS  # noqa: E402


# ----------------------------------------------------------
# 1) 200+ 검색어 + 9 그룹 키워드 + 다국어 포함
# ----------------------------------------------------------
def test_query_terms_at_least_200_with_all_groups():
    n = len(QUERY_TERMS)
    assert n >= 200, f"QUERY_TERMS 200개 이상 기대, 실제 {n}"

    # 중복 없음
    assert len(set(QUERY_TERMS)) == n, "QUERY_TERMS 내 중복 키워드 존재"

    # 9 그룹 대표 키워드 (각 그룹 1~3개 샘플) — 누락되면 실패
    must_have = [
        # 그룹 1: S 시리즈 풀세트
        "Galaxy S26", "Galaxy S2", "Galaxy S III",
        # 그룹 2: Note 시리즈 + 위기
        "Galaxy Note 7", "Note 7 explosion", "Note 7 ban",
        # 그룹 3: Z Fold/Flip + 결함
        "Galaxy Z Fold 8", "Galaxy Fold", "Fold display crease",
        "Galaxy Z Flip", "Flip hinge gap",
        # 그룹 4: Watch / Buds / Tab
        "Galaxy Watch Ultra", "Galaxy Watch Active",
        "Galaxy Buds Live", "Galaxy Ring", "Galaxy Tab S11",
        # 그룹 5: A / M
        "Galaxy A55", "Galaxy M series",
        # 그룹 6: SW / 생태계
        "One UI 8", "TouchWiz", "Galaxy AI", "SmartThings",
        # 그룹 7: 위기·이슈
        "Samsung GoS", "GoS throttling", "Galaxy S22 overheating",
        "Snapdragon vs Exynos", "Samsung S20 price",
        # 그룹 8: 비교
        "Galaxy vs iPhone", "Pixel vs Galaxy",
        # 그룹 9: 일반 + 다국어
        "samsung galaxy", "갤럭시", "갤럭시노트", "三星",
    ]
    missing = [k for k in must_have if k not in QUERY_TERMS]
    assert not missing, f"필수 키워드 누락: {missing}"

    # 다국어 키워드 1개 이상 포함 — 한국어/중국어 separately
    kr_terms = [t for t in QUERY_TERMS if any(0xAC00 <= ord(c) <= 0xD7A3 for c in t)]
    cn_terms = [
        t for t in QUERY_TERMS
        if any(0x4E00 <= ord(c) <= 0x9FFF for c in t)
        and not any(0xAC00 <= ord(c) <= 0xD7A3 for c in t)
    ]
    assert len(kr_terms) >= 3, f"한국어 키워드 3개 이상 기대, 실제 {len(kr_terms)}: {kr_terms}"
    assert len(cn_terms) >= 1, f"중국어 키워드 1개 이상 기대, 실제 {len(cn_terms)}: {cn_terms}"

    print(
        f"  [PASS] QUERY_TERMS={n} (KR={len(kr_terms)} CN={len(cn_terms)})"
    )


# ----------------------------------------------------------
# 2) _load_query_terms 동작 — HN_TERMS_FILE 우선
# ----------------------------------------------------------
def test_load_query_terms_env_override(tmp_path, monkeypatch):
    # 기본: 파일 미설정 → QUERY_TERMS 반환
    monkeypatch.delenv("HN_TERMS_FILE", raising=False)
    base = _load_query_terms()
    assert base == list(QUERY_TERMS), "환경변수 미설정 시 QUERY_TERMS 그대로 반환"

    # HN_TERMS_FILE 지정 → 파일 내용 사용
    f = tmp_path / "hn_terms.txt"
    f.write_text(
        "# 주석은 무시\n"
        "Galaxy Test1\n"
        "Galaxy Test2\n"
        "\n"
        "Galaxy Test3\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HN_TERMS_FILE", str(f))
    loaded = _load_query_terms()
    assert loaded == ["Galaxy Test1", "Galaxy Test2", "Galaxy Test3"], loaded
    print(f"  [PASS] HN_TERMS_FILE override → {len(loaded)} terms")


# ----------------------------------------------------------
# 3) backfill DEFAULT_TERMS 가 QUERY_TERMS superset
# ----------------------------------------------------------
def test_backfill_default_terms_superset_of_query_terms():
    missing = [t for t in QUERY_TERMS if t not in BACKFILL_TERMS]
    assert not missing, (
        f"backfill DEFAULT_TERMS 가 QUERY_TERMS 의 superset 이 아님 — 누락 {len(missing)}: "
        f"{missing[:5]} ..."
    )
    assert len(BACKFILL_TERMS) >= len(QUERY_TERMS), (
        f"DEFAULT_TERMS({len(BACKFILL_TERMS)}) >= QUERY_TERMS({len(QUERY_TERMS)}) 기대"
    )
    # 중복 없음
    assert len(set(BACKFILL_TERMS)) == len(BACKFILL_TERMS), (
        "DEFAULT_TERMS 내 중복 키워드 존재"
    )
    print(
        f"  [PASS] DEFAULT_TERMS={len(BACKFILL_TERMS)} >= QUERY_TERMS={len(QUERY_TERMS)}"
    )


if __name__ == "__main__":
    test_query_terms_at_least_200_with_all_groups()

    import tempfile
    import pathlib

    class _MP:
        def __init__(self):
            self._restore = []
            self._del = []

        def setenv(self, k, v):
            old = os.environ.get(k)
            self._restore.append((k, old))
            os.environ[k] = v

        def delenv(self, k, raising=False):
            if k in os.environ:
                self._restore.append((k, os.environ[k]))
                del os.environ[k]

        def undo(self):
            for k, v in reversed(self._restore):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    with tempfile.TemporaryDirectory() as td:
        mp = _MP()
        try:
            test_load_query_terms_env_override(pathlib.Path(td), mp)
        finally:
            mp.undo()

    test_backfill_default_terms_superset_of_query_terms()
    print("\nAll tests passed.")
