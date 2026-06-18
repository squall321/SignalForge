# SignalForge 현황 (2026-06-18 기준)

> 본 문서는 전부 실측(psql / ss / curl / ps / rclone / git) 기반이다. 추정값·과장은 배제했고, 측정상 한계와 잔여는 솔직히 기록한다.
> 측정 시점: 2026-06-18 약 08:50~09:30 UTC. 측정 당시 backend(18000)·frontend(17370)가 DOWN 이었으나 **재기동 후 런타임 HTTP 200 으로 재검증 완료** (endpoint 115·charts 5/5·MCP 16 라이브 확인). HWAX 포털(8088)은 별도 HWAXPortal 프로젝트 소관으로 현재 DOWN.

---

## 1. 한눈 요약

| 항목 | 값 | 비고 |
|---|---|---|
| voc_records total | 228,212 | 전체 누적 (수집 진행 중) |
| voc_records active | 116,129 | archived_at NULL |
| voc_records archived | 112,083 | 아카이브 49.1% |
| MX 매칭율 (active) | 80.5% (93,390건) | 임계 50% 통과 |
| MX rich (active, ≥100자) | 56.3% (65,360건) | 임계 40% 통과 |
| products | 389종 | 제품 태깅율 18.9% (active) |
| 등록 플랫폼 | 95개 | active 87 |
| 24h 수집 사이트 | 62개 | 7일 기준 74개 |
| 완전일 평균 수집 | 약 1,940건/일 | 최근 7일 |
| backend endpoint | 115개 | 라우터 15개 (정적 카탈로그) |
| MCP 도구 | 16개 | 차트 5종 포함 |
| frontend 페이지 | 12개 / 라우트 13개 | |
| Alembic head | 0019 | degraded 없음 |

**서비스 가동 상태 (포트 실측):** postgres(5434)·redis(6379)·MCP(8001)·celery worker/beat·backend(18000)·frontend(17370) = **UP** / HWAX 포털(8088, 별도 프로젝트) = **DOWN**.

---

## 2. 데이터 — 규모·품질·연도분포·언어

### 2.1 규모
- total 228,212 / active 116,129 / archived 112,083 (아카이브 49.1%). 측정 후 수집 진행으로 total/active 는 분 단위 증가 중(archived·비율은 고정).

### 2.2 MX 매칭 품질 (active 기준, endpoint 동일 공식 재현)
- mx_match 93,390건 = **80.5%** (baseline R5 75.8% 대비 +4.7pt)
- mx_rich(content ≥100자) 65,360건 = **56.3%** (baseline 51.1% 대비 +5.2pt)
- 두 임계(50% / 40%) 모두 통과.

### 2.3 제품 태깅
- products 389종. active voc 중 product_id 매핑 21,893건 = **18.9%** (7일 표본 기준 health 리포트는 20.6%, <30% 임계 미달 — 본질적 한계로 알려진 잔여).

### 2.4 published_at 연도 분포 (active)
- 옛 글(2007~2015) 누적 약 23,855건 확보.
- 2026년이 45,105건으로 전체의 38.9% 압도. published_at NULL 1,702건.
- 주요 연도: 2025 8,249 / 2024 6,034 / 2023 5,259 / 2019 4,843 / 2021 4,692.

### 2.5 sentiment 분포 (active)
| label | count | 비율 |
|---|---|---|
| positive | 37,330 | 32.2% |
| (NULL/미분류) | 32,811 | 28.3% |
| neutral | 30,612 | 26.4% |
| negative | 15,243 | 13.1% |

- sentiment 미분류(NULL) 28.3% 잔존.

### 2.6 언어·번역
- language_detected NULL 64,920건 = **56.0%** (언어 감지 미실행 잔재가 절반 이상). en 22,494 / ko 19,581 / id 1,660 / es 1,545.
- 미번역 잔재 중 실질 외국어 본문(non-ASCII) **9,372건**이 번역 공백. (미번역 65,229건 중 대부분은 lang NULL = 영어 가능성 포함.)

---

## 3. 수집 — 추세·사이트·DEAD·worker/beat

### 3.1 일별 추세 (최근 7일, 06-18은 부분일)
| 날짜 | 수집 | 날짜 | 수집 |
|---|---|---|---|
| 06-18 (부분일) | 765 | 06-14 | 1,704 |
| 06-17 | 1,990 | 06-13 | 1,823 |
| 06-16 | 2,112 | 06-12 | 2,272 |
| 06-15 | 2,096 | 06-11 | 1,598 |
- 완전일 평균 약 1,940건/일. 최근 수집분 100% 활성 (24h 신규 아카이브 0).

### 3.2 24h 사이트 Top (platforms 조인)
hackernews 696 · dcinside 385 · reddit_rss 189 · mlbpark 76 · lemmy 71 · quasarzone 47 · ppomppu 36 · mastodon 33 · fourchan_g 23 · xataka 21.

### 3.3 플랫폼 총계
- 전체 95 / is_active 87 / 24h 수집 62 / 7일 수집 74.
- collection_health status = **critical** (critical 9 / warning 1). 단 06-18은 부분일(08:50 측정)이라 cycle 후반 수집 사이트(computerbase/fmkorea 등)의 "24h 0건"이 일부 측정 시각 차이에 기인.

### 3.4 DEAD / 운영 주의
- **active=true인데 7일+ 무수집:** hackerone(06-09)·anandtech(06-08)·xda(06-07)·ifixit(06-06) — 차단/소스변경 추정.
- **active=true인데 수집 이력 0:** quora·bluesky·sspai·nu_nl — 키/인증/차단 부재.
- active=false 8개(amazon 4종·reddit·twitter·bestbuy·naver_cafe)는 의도적 비활성.
- **dogdrip 급감 점검 필요:** 평소 137.6건/일 사이트가 06-18 4건(2.9%)으로 급감.

### 3.5 worker / beat / 큐 (ps·redis 실측)
- worker 마스터 PID 12618 (`--concurrency=4`) + 자식 4 (12713/12715/12716/12717).
- beat PID 643316.
- Redis `LLEN celery` = 71 (안정, 정상 inflight). keyspace db0 = 2,044 keys.

---

## 4. 서비스 — backend·frontend·MCP·포털

> backend/frontend 재기동 후 런타임 재검증 완료: openapi.json 라이브 endpoint 115·charts 5/5 HTTP 200 확인. HWAX(8088)만 DOWN(별도 프로젝트).

### 4.1 가동 상태 (ss -tlnp)
| 서비스 | 포트 | 상태 |
|---|---|---|
| postgres | 5434 | UP (pid 12340) |
| redis | 6379 | UP (PONG) |
| MCP server | 8001 | UP (pid 12624) |
| celery worker/beat | — | UP |
| backend | 18000 | **UP** (재기동, /health 200) |
| frontend | 17370 | **UP** (재기동, /charts 200) |
| HWAX 포털 | 8088 | **DOWN** (HTTP 000, 별도 HWAXPortal 소관) |

### 4.2 backend endpoint 카탈로그 (라우터 15개, 총 **115개** · 라이브 openapi 재확인)
- _internal 37 · deep 21 · alerts 11 · analytics 9 · insights 8 · community 6 · charts 5 · geo 4 · kg 3 · products 3 · temporal 3 · crawl_jobs 2 · dashboard 1 · shared 1 · websocket 1.
- 라우터 파일 위치는 `backend/app/api/` (prefix `/api/v1` 부여). 메모리상 "67 endpoint" 대비 +48 (P4.2 이후 미기록 증가분).
- charts 5종 실제 경로: `/charts/sentiment-timeseries`·`/country-distribution`·`/category-distribution`·`/crisis-timeline`·`/keyword-network`.

### 4.3 frontend
- 페이지 12개(Alerts·ChartGallery·CollectionStatus·CommunityView·Compare·Dashboard·DataQuality·DeepInsights·GeoView·History·KnowledgeGraph·TemporalInsight).
- 라우트 13개(`/dashboard` 기본 + temporal·kg·geo·community·insights·alerts·compare·collection·history·data-quality·charts·`*`→dashboard).

### 4.4 MCP 도구 16개 (서버 :8001 가동)
- Tier 0/1 (11): query_voc·get_top_issues·search_voc·analyze_sentiment_trend·compare_products·get_country_breakdown·get_voc_summary·daily_briefing·alert_check·site_health·top_emerging_keywords.
- 차트 (5): chart_sentiment_timeseries·chart_country_distribution·chart_category_distribution·chart_crisis_timeline·chart_keyword_network.

### 4.5 데이터 정합 sanity
- VOC 실테이블명 = **`voc_records`** (+ `voc_active` MV, voc_categories/keywords/topics). spec의 `voc` 테이블명은 부정확.

---

## 5. 배포·동기화 — apptainer·Drive·git·이관

### 5.1 Apptainer SIF (6종, 약 993M)
crawler 433M · backend 145M · mcp 137M · postgres 103M · postgres-base 103M · frontend 72M. (요청 "5종"보다 postgres-base 1개 많음.)

### 5.2 Drive 동기화 (LATEST.json, schema auto_sync.v1)
- ts 2026-06-18T09:30:00Z, voc_count 227,919, last_dump sf-db-20260618-043001Z.sql.gz(약 95M, sha256 검증), dry_run=false.
- 드리프트: LATEST 227,919 vs 라이브 228,079 = +160 (30분 주기 수집 증가분, 정상).

### 5.3 git
- branch main, origin/main 대비 ahead 0 / behind 0 (동기).
- 미커밋 변경 52파일 (대부분 reports/* 자동 산출물 + .bkit/state).
- HEAD: b7b8d3f 번역 reprocess ASCII-only 오탐 제외.

### 5.4 이관 자산 (실재 확인)
- `scripts/bootstrap-new-server.sh`(6,272B, +x) · `scripts/sync-from-drive.sh`(12,864B, +x) · `docs/SERVER_SETUP.md`(4,907B).

### 5.5 Alembic
- head 0019 (degraded 없음).

### 5.6 Celery beat 주기 (crawler/celery_app.py)
| task | 주기 |
|---|---|
| auto-sync-to-drive | 매 30분 (DB dump→Drive + LATEST.json) |
| translation-reprocess | 2시간 (limit=2000) |
| verify-backup | 일 1회 20:00 UTC |

---

## 6. 아키텍처 한눈

```
[수집 계층]                  [저장]            [서빙]                [소비]
 95 플랫폼 collector  ──┐                ┌─ backend :18000 ──┬─ frontend :17370 (12페이지)
 celery worker(c=4)  ──┼─▶ postgres :5434 ┤   (115 endpoint)  └─ HWAX 포털 /signalforge/ :8088
 celery beat ─────────┘    voc_records   │
   ├ 수집 cycle             voc_active MV  └─ MCP server :8001 ─── 16 도구 (Claude/외부 소비)
   ├ auto-sync 30m          + redis :6379 (celery 큐)
   └ translation 2h
                              │
                              └─▶ rclone → Google Drive (LATEST.json, 30분 주기 DB dump 백업)
```
- 수집(worker/beat) → postgres 저장 → backend API/MCP 서빙 → frontend/포털/Claude 소비. 별도로 30분마다 Drive 백업 + LATEST.json 갱신, 일 1회 백업 무결성 검증.

---

## 7. 알려진 잔여 + 사용자 액션 대기

### 7.1 사용자 액션 대기 (키 입력 — 전부 `.env` EMPTY, graceful skip 중)
| 키 | 영향 |
|---|---|
| `ALERT_WEBHOOK_URL` (Slack) | 알림 webhook 미발송 |
| `EXTERNAL_API_KEY` (Groq) + BASE_URL/MODEL | LLM external 티어 비활성 (로컬 ollama fast/high만) |
| `BLUESKY_HANDLE` / `BLUESKY_PASSWORD` | bluesky 수집 불가 (DEAD) |

### 7.2 운영 잔여
- **HWAX 포털(8088) DOWN:** backend·frontend 는 재기동·런타임 200 복구 완료. HWAX 포털만 미기동(별도 HWAXPortal 프로젝트 소관). uvicorn 백그라운드 기동은 shell 종료와 함께 죽는 경우가 잦아 `setsid bash -c '... > log 2>&1' </dev/null` detach 필수 (운영 학습).
- 제품 태깅율 18.9%(7일 20.6%) — <30% 임계 미달, 본질적 한계.
- sentiment 미분류 28.3% / language NULL 56.0% — 후처리 미실행 잔재.
- 미번역 외국어 본문(non-ASCII) 9,372건 — translation-reprocess 배치가 점진 해소 중.
- DEAD 사이트: amazon 4종·reddit·twitter·bluesky·quora는 키/인증 부재(영구), xda·anandtech·ifixit·hackerone은 차단/소스변경.
- dogdrip 06-18 급감(2.9%) — 별도 점검 필요.

### 7.3 측정상 한계 (드리프트 주의)
- 한눈 요약/2.1 수치는 backend 재기동 후 `/data-quality` 라이브 재확인값(total 228,212·active 116,129·mx 80.5%·rich 56.3%). 2.4~2.6 등 세부 분포는 최초 측정(08:50~09:30) 시점 psql 값이라 수집 진행분만큼 절대수가 소폭 낮을 수 있음(비율·구조는 유효).
- zdnet_kr: health JSON 24h 0건 critical vs 실측 24h 1건 (측정 시각 차이).
- 06-18 부분일(08:50 측정)이라 cycle 후반 사이트의 "24h 0건" critical 일부는 측정 시각 이전 미수집.

---

## 8. 최근 마일스톤 타임라인

| 시점 | 마일스톤 | 핵심 |
|---|---|---|
| 2026-06-10 (fe9dd7c) | r6 옛글 백필 | HN 연도 슬라이싱 +51k(37k→88k)·active +35,842·mx_match 75.7→83.9%·KR clien 옛글 +863·P0 worker REDIS_URL 복구 |
| ~2026-06-12 (fc70888~ddd13be) | 운영 자동화·이식성 | validator hook·health 자동 갱신·PROJECT.conf 상대경로화·bootstrap-new-server.sh 원샷 |
| 2026-06-12 (efb6ddb~070724f) | MCP 차트 규격화 | voc_active 정합성(약 2배 부풀림 해소)·echarts_option 표준·도구 4종+keyword_network·crisis GN7 정합 |
| (aeb7075~904a5d9) | 차트 갤러리 frontend | backend /charts/* + 페이지·전기간(2007~) 선택·거시·미시 풀 드릴다운 |
| (0c972f7~b7b8d3f, HEAD) | 번역 오탐 정리 | 배치 6h/500→2h/2000·ASCII-only 오탐 제외(잔재 68%가 가짜)·실패율 75%→0.6% 정상화 |

**작업 흐름:** r6 옛글 백필 → 운영 자동화·이식성 정비 → MCP 차트 규격화 → 차트 갤러리 frontend → 번역 오탐 정리(HEAD).
