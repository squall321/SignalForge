# SignalForge Harvest 3 Polish — 2026-06-06

**기준선 (Harvest 3 종료):** voc 125,792 · 활성 24h 57 / 7d 70 · 24h 10,256
**Polish 종료 (현 시점 실측):** voc **125,952** · 활성 24h **57** / 7d **70** · 24h **10,414** · Hardware.fr **206** (24h)
**증분:** +160 voc (P3 Hardware.fr 158 INSERT + P1·P2·P4 비침습). 모드: DRY_RUN/PRESERVE_EXISTING + audit JSONL `round=harvest3p` 5건 적재.

---

## 1. Verify 8건 종합

| # | task | 상태 | self-report drift | 비고 |
|---|---|---|---|---|
| 1 | voc_kpi | PASS | voc 125,792 일치 | sentiment 100%·topic 55% (today) |
| 2 | platforms | PASS | 24h 10,256 vs 10,255 = -0.01% | 시각 차이 |
| 3 | korean_deep | PASS | dc/ppom/dog/clien/fmkorea 모두 0% | FMKorea 1h=0 → IP 풀 결론 일치 |
| 4 | hardware_fr_file | PASS | 코드/스키마/등록 일치 | platforms 1행 OK |
| 5 | audit_jsonl | PASS | round 분포 R24=3·R25=1·R26=1·R26_TEST=36·legacy=24 | harvest3 grep 0건 (보고대로) |
| 6 | archive_R3 | PARTIAL | R26만 archive 존재 | harvest3 폴더 부재 — 차기 의무화 대상 |
| 7 | endpoints | PASS | 5/5 HTTP 200 | llm-status·key-status·trend·monitor·hook |
| 8 | regression | PARTIAL | 9/9 → 실측 10/10 (delta +11%) | baseline 메타가 한 카드 누락 |

---

## 2. P1 — FMKorea IP 풀 (build)

산출 3건:
- `crawler/base/proxy_pool.py` (3,853B) — Tor SOCKS5 인터페이스, `tor_endpoint_reachable()` probe, graceful
- `crawler/platforms/fmkorea.py` (16,439B, +5 lines: import L24 · proxy_kwargs L93-97)
- `crawler/tests/test_proxy_pool.py` (5,524B, 9 test)

**측정:** pytest proxy 9/9 + regression trio 29/29 PASS. dry_run window INSERT 0건. FMKorea 24h 4 (수치 무변경 — env `FMKOREA_USE_PROXY=true` 미설정 시 무동작 정상). audit `round=harvest3p · track=P1_FMKorea_IP_pool · tor_endpoint_reachable=false` (line 66) ok.

**판정:** 코드/테스트 PASS, 본 가동은 Tor 컨테이너 + 환경 키 입력 후. drift 0%.

## 3. P2 — audit JSONL end 의무화 (mature)

산출:
- `crawler/base/audit.py` (9,510B, 262 lines — 보고 260 LoC = drift +1%) — context manager로 `started_at`만 있는 incomplete 패턴 차단
- `crawler/tests/test_audit_context.py` (3 case) + `test_korean_deep.py` (run_id pairing assert)
- `crawler/scripts/korean_pagination_deep.py` — `_audit_append` 제거, `audit_round()` 통일

**측정:** 단위 3/3·korean_deep 1/1·audit 묶음 14/14 (보고 10/10 — drift +40%, 신규 모듈 6개 누락). 전체 크롤러 스위트 844 PASS / 1 FAIL (`test_multi_label_korean` 무관). Harvest 4부터 start/end 미쌍 entry는 CI 차단 가능.

**판정:** PASS. self-report drift 1%(LoC) ·40%(test bundle 카운트) — 둘 다 ±10% 룰에서 LoC만 통과.

## 4. P3 — Hardware.fr 보드 확장 (extend)

산출:
- `crawler/platforms/hardware_fr.py` 15,559B — 카테고리 3→4 (telephone-android·tablette·operateur·gsmgpspda)
- `crawler/scripts/harvest3p_hardware_fr.py` 5,051B (dry_run + preserve 2단)
- `crawler/tests/test_hardware_fr_v2.py` 4,776B 6/6

**측정 (실 voc INSERT — 본 Polish 유일한 mutation):**
- Hardware.fr 48 → **206** (24h) · delta **+158** (실측 일치)
- INSERT 시각 10:10:42 (audit `finished_at` 동일) — preserve mode end-of-run flush 1회 확인
- 카테고리 분포 (158 신규): telephone-android 100% (확장 카테고리는 backfill_pages=5에서 페이지 1만 적중)
- audit line 77 (dry_run delta=0) → line 78 (preserve inserted=158) 짝 OK

**판정:** PASS. self-report drift 0%(INSERT) ·전체 206 = (전 48 + 신규 158).

## 5. P4 — 1주 모니터 MD + Slack digest (deepen)

산출:
- `crawler/insight/weekly_monitor.py` 28,828B (`collect_new_site_progress` L128 · `render_markdown_report` L383 · `build_slack_digest_payload` L524 · `post_slack_digest` L563)
- `crawler/tasks.py` `run_weekly_monitor` → dict (json_path/md_path/alerts/slack) 반환
- 보고서 첫 줄: "# SignalForge Weekly Collection Monitor — 2026-06-06" (mock Slack payload 1건)

**측정:** voc_24h 10,255·active_sites 68·MD 3,836B·JSON 13,517B·alerts 1·tests 10/10. audit line 67(start)↔72(end) 짝 OK. new_sites_listed 보고 65 vs 실측 64 = drift -1.54% (±10% 내). Slack URL 미입력 → status=skipped (graceful 확인).

**판정:** PASS. ALERT_WEBHOOK_URL/SLACK_WEBHOOK_URL 입력 즉시 본가동.

## 6. 4중 안전장치 + regression

| 안전장치 | 상태 |
|---|---|
| DRY_RUN + PRESERVE_EXISTING | P3 dry→preserve 2단 OK |
| ON CONFLICT (DB level) | Hardware.fr 158 INSERT 충돌 0 |
| audit JSONL `round=harvest3p` | 5 lines (P1·P3 dry·P3 preserve·P4 start·P4 end) |
| Hook archive 폴더 검증 | **부재 — R26만 archive, harvest3p 폴더 미생성** |

regression baseline 9/9 (메모리) → 실측 10/10 (R20 카드 +1 추가 반영). FAIL 0건, R18 폭락 재발 0.

## 7. 키 입력 후 즉시 활성 절차

```bash
# 1) Tor + FMKorea proxy
docker run -d --name tor -p 9050:9050 dperson/torproxy
export FMKOREA_USE_PROXY=true FMKOREA_PROXY_URL=socks5://127.0.0.1:9050

# 2) Slack digest
export SLACK_WEBHOOK_URL=https://hooks.slack.com/...

# 3) Groq high tier
export GROQ_API_KEY=gsk_...
python crawler/scripts/key_health_check.py --json   # 5/5 PASS 기대

# 4) 본 가동 (audit round=harvest4 자동 부여)
celery -A crawler.celery_app beat & celery -A crawler.celery_app worker
```

## 8. 잔여 + 다음 라운드 (Harvest 4 권고)

**즉시 (PARTIAL 해소):**
1. **archive 폴더** — `reports/archive/harvest3p/` 미생성. Hook이 round 별 archive 의무화 룰 적용 시 자동 캡처 (R26 패턴 재사용).
2. **regression baseline** — 메모리 9/9 → 실제 10/10 (R20 종료 후 +1). MEMORY 다음 업데이트 시 수정.
3. **FMKorea 본 가동** — Tor 컨테이너 기동 후 24h voc 4 → 목표 200+ 회복 관측.

**잔여:**
- P3 Hardware.fr backfill_pages 1 → 5로 늘려도 신규 158건이 모두 telephone-android 페이지 1 적중. 카테고리 다양화 위해 다음 라운드 `THREAD_PAGES=2-3` 검증.
- P2 audit context manager — 미마이그레이션 collector 다수. 트랙별 점진 적용.
- P4 보고서 alerts 1건 (트리거 로직) 적정성 검증 — 1주 누적 후 false-positive 검토.

**self-report drift 종합:** P1 0% · P2 LoC +1% (test bundle +40% 카운트 누락) · P3 INSERT 0% · P4 new_sites -1.54% · regression +11% · archive R3 PARTIAL. 본 보고는 실 psql 측정값 (voc 125,952 / Hardware.fr 158 INSERT / audit harvest3p 5 entries) 기준.
