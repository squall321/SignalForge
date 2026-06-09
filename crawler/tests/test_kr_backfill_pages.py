"""KR 사이트 (clien/ppomppu/dcinside) BACKFILL_PAGES 환경변수 단위 테스트.

각 사이트의 LIST_PAGES 가 환경변수로 오버라이드 되는지만 검증.
외부 네트워크 / DB 호출 없음.

실행:
  cd crawler && python -m pytest tests/test_kr_backfill_pages.py -v
  cd crawler && python tests/test_kr_backfill_pages.py
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _reload(modname: str):
    """env 가 module-level 에서 읽히므로 reload 로 재평가."""
    if modname in sys.modules:
        return importlib.reload(sys.modules[modname])
    return importlib.import_module(modname)


def _run_with_env(env: dict, *modnames: str):
    saved = {k: os.environ.get(k) for k in env}
    try:
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        mods = [_reload(m) for m in modnames]
        return mods
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------- 1) 기본값 (env 없음) ----------
def test_default_list_pages_is_12():
    mods = _run_with_env({
        "CLIEN_BACKFILL_PAGES":    None,
        "PPOMPPU_BACKFILL_PAGES":  None,
        "DCINSIDE_BACKFILL_PAGES": None,
    }, "platforms.clien", "platforms.ppomppu", "platforms.dcinside")
    clien, ppomppu, dcinside = mods
    assert clien.LIST_PAGES == 12, f"clien default={clien.LIST_PAGES}"
    assert ppomppu.LIST_PAGES == 12, f"ppomppu default={ppomppu.LIST_PAGES}"
    assert dcinside.LIST_PAGES == 12, f"dcinside default={dcinside.LIST_PAGES}"
    print(f"  [PASS] 기본 LIST_PAGES=12 (3 sites)")


# ---------- 2) BACKFILL_PAGES=50 오버라이드 ----------
def test_backfill_pages_override():
    mods = _run_with_env({
        "CLIEN_BACKFILL_PAGES":    "50",
        "PPOMPPU_BACKFILL_PAGES":  "50",
        "DCINSIDE_BACKFILL_PAGES": "50",
    }, "platforms.clien", "platforms.ppomppu", "platforms.dcinside")
    clien, ppomppu, dcinside = mods
    assert clien.LIST_PAGES == 50
    assert ppomppu.LIST_PAGES == 50
    assert dcinside.LIST_PAGES == 50
    print(f"  [PASS] BACKFILL_PAGES=50 적용 (3 sites)")


# ---------- 3) 잘못된 값 → 기본값 fallback, 0/음수 → min_value(1) clamp ----------
def test_invalid_and_min_clamp():
    # 잘못된 문자열 → default 12
    mods = _run_with_env({
        "CLIEN_BACKFILL_PAGES":    "abc",
        "PPOMPPU_BACKFILL_PAGES":  "",
        "DCINSIDE_BACKFILL_PAGES": "not-a-number",
    }, "platforms.clien", "platforms.ppomppu", "platforms.dcinside")
    clien, ppomppu, dcinside = mods
    assert clien.LIST_PAGES == 12, f"잘못된 값 → default: {clien.LIST_PAGES}"
    # 빈문자열은 env 로 보면 unset 과 같음 (getenv("") not truthy in 로직)
    assert ppomppu.LIST_PAGES == 12
    assert dcinside.LIST_PAGES == 12

    # 0 / 음수 → clamp to 1
    mods = _run_with_env({
        "CLIEN_BACKFILL_PAGES":    "0",
        "PPOMPPU_BACKFILL_PAGES":  "-5",
        "DCINSIDE_BACKFILL_PAGES": "1",
    }, "platforms.clien", "platforms.ppomppu", "platforms.dcinside")
    clien, ppomppu, dcinside = mods
    assert clien.LIST_PAGES == 1, f"0 → clamp 1, 실제={clien.LIST_PAGES}"
    assert ppomppu.LIST_PAGES == 1, f"-5 → clamp 1, 실제={ppomppu.LIST_PAGES}"
    assert dcinside.LIST_PAGES == 1
    print(f"  [PASS] 잘못된 값 fallback / min clamp")


if __name__ == "__main__":
    tests = [
        test_default_list_pages_is_12,
        test_backfill_pages_override,
        test_invalid_and_min_clamp,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n결과: {len(tests) - failed}/{len(tests)} 통과")
    sys.exit(0 if failed == 0 else 1)
