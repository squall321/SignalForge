"""R23 트랙 D — Crisis platform 본런 *연속 실행* 안전성 단위.

R22 에서 engadget/theverge/androidcentral 각 1회 본런 완료 후, R23 에서
한 platform 을 추가 본런하면 어떻게 동작하는지 *재현 가능한 단위* 로 검증.

핵심:
  PRESERVE_EXISTING=true 모드 = save 가 ON CONFLICT DO NOTHING 으로 처리되므로
  *동일 external_id* 의 voc 는 두 번째 본런에서 0 saved (중복 차단).
  즉, R23 본런은 *신규* article 만 실질 저장한다.

이 단위는 외부 네트워크/DB 없이 _save_via_crawler 의 dedup + DRY_RUN gating 만
확인한다 — 멱등성 보장 핵심.

실행:
  cd crawler && python -m pytest tests/test_crisis_continued.py -v
  cd crawler && python tests/test_crisis_continued.py
"""
import asyncio
import importlib
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC  # noqa: E402


def _make_raw(idx: int) -> RawVOC:
    """test 용 RawVOC fixture — 외부 의존 없음."""
    return RawVOC(
        external_id=f"r23-cont-{idx:02d}",
        content=f"Galaxy Note 7 recall continued case {idx} — dummy body " * 5,
        source_url=f"https://example.com/case-{idx}",
        author_name="test-author",
        published_at=datetime(2016, 9, 10 + (idx % 3), 12, 0, 0, tzinfo=timezone.utc),
        country_code="US",
        meta={"kind": "article", "source": "crisis_direct",
              "product_code": "GN7", "crisis_code": "GN7"},
    )


def test_dry_run_skips_save():
    """DRY_RUN=1 일 때 _save_via_crawler 가 0 saved 반환 (network/db 미접촉)."""
    # CPD_DRY_RUN=1 강제 후 모듈 reload — 모듈 레벨 변수 갱신
    os.environ["CPD_DRY_RUN"] = "1"
    if "scripts.crisis_platform_direct" in sys.modules:
        importlib.reload(sys.modules["scripts.crisis_platform_direct"])
    from scripts import crisis_platform_direct as cpd  # noqa: E402

    assert cpd.DRY_RUN is True, "CPD_DRY_RUN=1 미반영"

    fakes = [_make_raw(i) for i in range(3)]

    class _FakeCrawler:  # save 가 호출되면 안 됨
        async def save(self, *a, **kw):
            raise AssertionError("DRY_RUN 인데 save 호출됨")

        def normalize(self, raw):
            return raw

    info = asyncio.run(cpd._save_via_crawler(_FakeCrawler(), fakes))
    assert info["saved"] == 0
    assert info["dry_run"] == 1
    print(f"  [PASS] DRY_RUN gating: {info}")


def test_dedup_within_batch():
    """동일 external_id 가 batch 내 중복 시 1건만 normalize/process."""
    # 실 save 경로 진입을 위해 DRY_RUN=0 으로 전환 + 모듈 reload
    os.environ["CPD_DRY_RUN"] = "0"
    os.environ["CPD_PRESERVE_EXISTING"] = "1"
    if "scripts.crisis_platform_direct" in sys.modules:
        importlib.reload(sys.modules["scripts.crisis_platform_direct"])
    from scripts import crisis_platform_direct as cpd  # noqa: E402

    assert cpd.DRY_RUN is False
    assert cpd.PRESERVE_EXISTING is True

    # 동일 external_id 3건 + 신규 2건 → dedup 후 3건 (1 + 2)
    dup_raw = _make_raw(1)
    fakes = [dup_raw, dup_raw, dup_raw, _make_raw(2), _make_raw(3)]

    seen_normalize: list = []
    seen_save: list = []

    class _RecCrawler:
        # R25 트랙 D — _save_via_crawler 가 crawler.platform_code 를 참조하므로
        # stub 에도 속성 필요 (실 DB 없을 땐 _query_inserted_ids 가 빈 list 반환).
        platform_code = "TEST_PLATFORM"

        def normalize(self, raw):
            seen_normalize.append(raw.external_id)
            return raw

        async def save(self, processed):
            seen_save.extend([p.external_id for p in processed])
            return len(processed)

    # nlp.pipeline.process_voc_list 가 외부 의존 — 단위 위해 monkeypatch
    import nlp.pipeline as nlp_pipe  # noqa: E402
    original = nlp_pipe.process_voc_list

    async def _identity_pipeline(std):
        return std
    nlp_pipe.process_voc_list = _identity_pipeline

    # R25 트랙 D — _query_inserted_ids 는 DATABASE_URL 미설정 시 빈 list 반환하지만,
    # CI 환경에 DATABASE_URL 이 떠 있을 가능성에 대비해 명시적으로 monkeypatch.
    async def _empty_query(*a, **kw):
        return []
    original_query = cpd._query_inserted_ids
    cpd._query_inserted_ids = _empty_query

    # _max_voc_id 도 외부 의존 차단 (실 DB 접속 회피)
    async def _zero_max(*a, **kw):
        return 0
    original_max = cpd._max_voc_id
    cpd._max_voc_id = _zero_max
    try:
        info = asyncio.run(cpd._save_via_crawler(_RecCrawler(), fakes))
    finally:
        nlp_pipe.process_voc_list = original
        cpd._query_inserted_ids = original_query
        cpd._max_voc_id = original_max

    # dedup: 5 raw → 3 uniq (external_id 1/2/3)
    assert len(seen_normalize) == 3, f"normalize call 횟수: {seen_normalize}"
    assert len(seen_save) == 3
    assert info["saved"] == 3
    assert info["processed"] == 3
    print(f"  [PASS] dedup 5→3: normalize={len(seen_normalize)} saved={info['saved']}")


def test_environment_defaults_safe():
    """환경변수 미설정 시 DRY_RUN/PRESERVE_EXISTING 안전 default."""
    # 환경 격리 — CPD_* 제거 후 reload
    for k in list(os.environ):
        if k.startswith("CPD_"):
            del os.environ[k]
    if "scripts.crisis_platform_direct" in sys.modules:
        importlib.reload(sys.modules["scripts.crisis_platform_direct"])
    from scripts import crisis_platform_direct as cpd  # noqa: E402

    assert cpd.DRY_RUN is True, "기본값이 DRY_RUN=true 여야 함 (안전 가드)"
    assert cpd.PRESERVE_EXISTING is True, "기본값이 PRESERVE_EXISTING=true 여야 함"
    assert cpd.PLATFORM_ARG == "9to5google"
    print(f"  [PASS] default DRY_RUN={cpd.DRY_RUN} PRESERVE={cpd.PRESERVE_EXISTING}")


if __name__ == "__main__":
    tests = [
        test_dry_run_skips_save,
        test_dedup_within_batch,
        test_environment_defaults_safe,
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
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n결과: {len(tests) - failed}/{len(tests)} 통과")
    sys.exit(0 if failed == 0 else 1)
