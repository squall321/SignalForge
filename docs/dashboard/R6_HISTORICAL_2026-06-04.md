# R6 옛 디바이스 커버리지 — 통합 보고 (2026-06-04)

작업 일시: 2026-06-03 ~ 2026-06-04 KST
범위: products 마스터 17년 확장 · HN 검색어 확장 · HN Algolia 전기간 backfill · product_id NULL re-link
DB: `127.0.0.1:5434/signalforge` (PostgreSQL 16.14)
Alembic head: `0009`

---

## 1. 트랙별 결과 + verify 결과

| 트랙 | 상태 | verify 평결 | 주요 산출 |
|------|------|--------------|-----------|
| A. products 마스터 확장 | **PASS** | 모든 클레임 독립 검증 통과. total=122, dated=113, undated 9개 정확 일치. pytest 1/1. | `0008_legacy_products.py`, `0009_products_release_boost.py`, `tests/test_products_historical.py` |
| B. HN/collector 검색어 확장 | **PARTIAL** | 검색어 수·정규식 보강·crawl() 동작 PASS. 다만 보고서의 매칭 건수(545/536)는 실측(~380/375) 대비 **약 43% 과대** — 측정 오차 허용범위(±20%) 초과. | `hackernews.py` QUERY_TERMS 80개, `categorizer.py` GALAXY_MODEL_RE 보강, `test_hackernews_search_v3.py`, `test_categorizer_historical.py` |
| C. HN Algolia 전기간 backfill | **PASS** | dry-run 정상, numericFilters 부재 확인, story+comment 양쪽 호출, 디덥 정상. pytest 2/2. | `scripts/hn_backfill_alltime.py`, `tests/test_hn_backfill.py`, `/tmp/hn_backfill.log` |
| D. product_id NULL re-link | **PASS** (경미) | 핵심 pytest 6/6, 옛 모델 hit 분포 표 보고치와 완전 일치. unknown_code=0. 전체 hit rate 1.74%/dry-run vs 1.56%/full — 허용범위. | `scripts/relink_products.py`, `tests/test_relink.py` |

## 2. products 마스터 확장 (48 → 122)

- 17년 Galaxy 라인업 전수 커버: GS 39 (S1~S26U) · GN 12 (Note 1~20U) · GZ 15 (Fold/Flip 1~8) · GW 11 (Watch 1~Ultra) · GB 9 (Buds 1~Buds 4 Pro)
- iPhone 14개 (6~16PM), Pixel 11개 (1~9 + 8P/9P)
- 미공개 9개(undated) = whitelist 정확 일치: `{GB4, GB4P, GFE25, GR2, GS26, GS26P, GS26U, GZF8, GZFL8}`
- 적용 방식: `ON CONFLICT (code) DO UPDATE` 멱등. 옛 디바이스 `is_active=False`로 신규 수집 대상에서 제외.

## 3. 검색어 확장 (50+ → 80)

- `crawler/platforms/hackernews.py` QUERY_TERMS: 그룹 5(옛 모델 18개) + 그룹 6(위기·비교 12개) 추가
- `crawler/nlp/categorizer.py` `GALAXY_MODEL_RE`: Note Edge / S\d+ Edge / S\d+ 5G / Active / FE / Buds Live / Watch Active / J\d / A\d 1자리 패턴 보강
- 단위 테스트: `test_query_terms_v3_expanded` (≥80 검색어 + Note 7 / S10 5G / Samsung 키워드 검증)
- 알려진 노이즈: "YC S20"(YCombinator batch) 가 옛 S20과 충돌 — 별도 후속

## 4. HN backfill 결과

- 검색어 84개 × {story, comment}, 페이지당 1000 hit, numericFilters 미적용 (전기간)
- 수집: 33,138건 RawVOC (story 8,060 / comment 25,078)
- 적재 후 voc_records 중 platform_id=21 (HN) 행: **33,528건**
- 카테고리 분포(unnest): others 440 · comparison 347 · software 301 · ai_features 210 · price 200 · build_quality 150 · display 145 · performance 124
- HN 행 중 product_id 매칭된 행 = 98건 (낮음 — HN 영어 토큰에서 모델 정규식 hit 자체가 적음)

## 5. re-link 결과

- 적용 전: voc_records.product_id NULL 106,224건
- 적용 후: NULL 104,564 / 채움 1,660건 (full-run 기준)
- 현재 DB 스냅샷: voc_records 161,359 / linked 24,656 / null 136,703  
  → 트랙 D 적용 이후 HN backfill(트랙 C)로 신규 행이 추가되어 NULL이 다시 증가. C·D 둘 다 적용된 최종 상태에선 re-link 재실행 필요.
- 다음 단계 후보 (§9 참조).

## 6. 옛 모델별 voc 매칭 표 (DB 직접 조회)

| code | 모델 | hits |
|------|------|------|
| GS2 | Galaxy S2 | 191 |
| GN9 | Galaxy Note 9 | 93 |
| GN20U | Galaxy Note 20 Ultra | 87 |
| GZF1 | Galaxy Fold 1 | 80 |
| GB1 | Galaxy Buds 1 | 67 |
| GN10 | Galaxy Note 10 | 63 |
| GS105G | Galaxy S10 5G | 34 |
| GN7 | Galaxy Note 7 | 30 |
| GS10 | Galaxy S10 | 18 |

(보고서의 분포 클레임과 완전 일치 — verify PASS 근거)

## 7. 가동 절차

```bash
# 0. 마이그레이션 (멱등)
cd /home/koopark/claude/SignalForge/backend
.venv/bin/alembic upgrade head     # → 0009 (head)

# 1. HN 전기간 backfill (페이지 수는 환경변수로 조절)
cd /home/koopark/claude/SignalForge/crawler
BACKFILL_MAX_PAGES=3 BACKFILL_HITS_PER_PAGE=1000 BACKFILL_SLEEP=1.0 \
  DATABASE_URL='postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge' \
  /home/koopark/claude/SignalForge/.venv/bin/python -m scripts.hn_backfill_alltime

# 2. product_id NULL re-link (옛 모델 매칭)
/home/koopark/claude/SignalForge/.venv/bin/python -m scripts.relink_products

# 3. 검증
cd /home/koopark/claude/SignalForge/backend && PYTHONPATH=. .venv/bin/pytest tests/test_products_historical.py -v
cd /home/koopark/claude/SignalForge/crawler && /home/koopark/claude/SignalForge/.venv/bin/pytest \
  tests/test_hackernews_search_v3.py tests/test_categorizer_historical.py \
  tests/test_hn_backfill.py tests/test_relink.py -v
```

## 8. 측정 수치 종합 (DB 실측, 2026-06-04 KST)

| 항목 | 값 |
|------|-----|
| products total | 122 (이전 48) |
| products dated | 113 |
| products undated | 9 (whitelist 정확 일치) |
| voc_records total | 161,359 |
| voc_records linked (product_id NOT NULL) | 24,656 |
| voc_records NULL product_id | 136,703 |
| voc_records HN(platform_id=21) | 33,528 |
| voc_records HN linked | 98 |
| HN backfill 수집 (RawVOC) | 33,138 (story 8,060 + comment 25,078) |
| HN 검색어 | 84 (그룹 5+6 추가 후) |
| 트랙 D re-link 채움 (전·후) | 106,224 → 104,564 (-1,660) |
| pytest (backend products_historical) | 1/1 PASS |
| pytest (crawler 4 파일 합산) | 12/12 PASS |

## 9. 다음 단계

1. **트랙 D 재실행** — 트랙 C로 신규 들어온 HN 33,528행 중 NULL 잔여분에 대해 `relink_products.py` 재가동.
2. **YC batch 노이즈 필터** — "YC S20/F20" 등 검색어/정규식 충돌건 negative pattern 추가.
3. **트랙 B 측정 재현** — 보고서 매칭 건수(545/536) 산출 방식과 실측(380/375) 차이 원인 추적 (필터 조건? 시점차?).
4. **HN 외 옛 모델 backfill** — Reddit/Bluesky/포럼도 옛 모델 검색어로 backfill 적용.
5. **GR2(Galaxy Ring 2) 등 미발매 9종** — release_at 확정 시점에 0010 마이그레이션으로 갱신.
