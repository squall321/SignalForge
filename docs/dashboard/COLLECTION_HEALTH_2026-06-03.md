# 수집 헬스 진단 — 2026-06-03 KST 01:25

## 1. 한 줄 결론

**degraded (주황)** — 인프라(Celery·MV·LLM)는 살아 있고 KR 권역 수집은 강력하나, **사이트 31/72 정체 (xda 18일 dead, reddit/twitter 3.5일 stale, never_collected 7개)** + **알림 cooldown 사실상 무력화로 7시간에 304건 폭주** + **Instiz 잠금 안내문 5,505건 노이즈 + categories 미할당 65.93%** 가 동시 진행 중이다.

## 2. 6 차원 종합 표

| 차원 | 수치 (실측) | 신호등 | 비고 |
|---|---|---|---|
| 1. Celery worker·beat | beat etime 18h 37m, worker etime 18h 35m (concurrency=4), `LLEN celery`=0, beat log mtime 01:24:54, 스케줄 30+건 등록 | healthy | 백로그 없음, 즉시 소화 |
| 2. 사이트별 수집 | 활성 72 중 alive 41 (57%), stale 23 (32%), dead 1 (xda 431h≈18일), never_collected 7 (amazon×4·bestbuy·kaskus·naver_cafe), reddit 83h·twitter 83h·hackernews 101h stale | degraded | reddit/twitter 3.5일 결측, AU 2개 70h |
| 3. MV 신선도·refresh | 5종 MV latest=2026-06-03, platform_health lag 35m 12s, 수동 REFRESH 합 491ms, beat 30분 주기 (refresh_mv_voc_daily 95ms succeeded) | healthy | tasks.py 80ms 가설 부합, voc lag 1m 54s |
| 4. 데이터 품질 (7d 79,454) | UNIQUE 위반 0, content<10자 8.14%, language NULL 0, sentiment NULL 0, engagement NULL 24 (0.03%), Instiz 잠금문 5,505건(7d의 6.93%) 단일 패턴, categories 빈 65.93% | degraded | 핵심 NULL ≤0.03% 양호, 노이즈 패턴·미분류 다수 |
| 5. 알림 발화 | 7d 312건 전부 rule 3, rule 1·2 발화 0, cooldown 900s 인데 연속발화 311쌍 중 309쌍(99.4%) 900s 이내, 평균 간격 120s·최소 0s, value 평균 567.59 (임계 20의 32×), 06-03 02~08시 304건 집중 | degraded | cooldown 미작동·임계 미조정 |
| 6. 운영·로그·LLM | /tmp/sf_backend.log 24h ERROR 0, cache hit 0.6508 (792/425), grounding 0.3486 (06-01 0.3456·06-02 0.3515), slack dry_run last 01-1.5h 전, alerts.log `site_dead: reddit` 25회/25h 반복, daily insight 06-02 ✅, 06-03 09:30 예정 | degraded | LLM grounding 0.35 (P4.2 주장 0.68 갭 지속), reddit 사망 반복 알림 |

**종합 신호등: degraded** (broken 차원 0, degraded 4, healthy 2 → 종합 degraded).

## 3. 위험 신호 TOP 5

1. **알림 cooldown 무력화** — 7일 312건 중 311쌍 분석 결과 99.4%가 900s 미만 간격, 최소 0s 동일 payload 반복 기록. 06-03 02-08시 단 6시간에 304건 집중. Redis dedup key TTL 또는 rule 3 evaluation loop 점검 시급.
2. **xda 18일 dead + reddit/twitter 3.5일 stale** — 전체 dead 1개지만 글로벌 핵심 소스 reddit(83h)·twitter(83h, 7d=1건으로 사실상 무력화)·hackernews(101h) 동반 정체. alerts.log `site_dead: reddit` 25회 발화는 정확히 이 상태 감지 중.
3. **never_collected 7 사이트** — amazon_de/jp/kr/us·bestbuy·kaskus·naver_cafe 가 수집 이력 0. 콜렉터 미가동 또는 등록만 되고 task 미스케줄 의심.
4. **Instiz 잠금 안내문 5,505건** — 단일 문구 `'1시간 내 작성된 댓글은…'`이 7일 record의 6.93%. 분석 분모 왜곡, content_original 중복 7,528 그룹 중 최대 비중.
5. **LLM grounding 0.3486 + P4.2 보고서 갭** — 7일 history (0.3456→0.3515→0.3486)로 변동 거의 없음. P4.2 final 의 0.68 주장과 0.33pt 갭 지속, 다운스트림 인사이트 신뢰도에 미반영.

## 4. 즉시 조치 권고

1. **alerts 5분 beat cooldown 로직 점검** — `tasks.run_alert_check` 의 redis key (`alert:dedup:rule3:*`) 존재 여부·TTL 실측, 없으면 set NX EX 900 추가. 임계 20 → 200+ 상향 별건 진행.
2. **reddit/twitter collector 재기동·헬스체크** — 24h 결측 누적, alerts 25회 발화에도 자동 복구 미실행. crawler/collectors/{reddit,twitter}.py 로그 확보, API 자격 만료 여부 확인.
3. **Instiz collector 필터 추가** — `content_original LIKE '1시간 내 작성된 댓글은%'` drop 룰을 수집 단계에 삽입.
4. **never_collected 7개 분류** — (a) 의도적 비활성이면 platforms.is_active=false, (b) 활성 유지면 beat 스케줄 엔트리 추가 + collector 구현 확인.
5. **rule 1·2 (anomaly_z·negative_rate) 임계 재산정** — 7일 0건은 임계 과대 또는 metric_path 미공급. community.extreme_negative_count·negative_rate_max 실측치 분포 점검.

## 5. 7일 추세 (수집량·발화·grounding)

- 수집량: 7일 누적 79,454 (일평균 11.4k). 일별 분해는 미산출이나 1h=519(instiz 335+computerbase 64+ppomppu 58+…)·24h=4,869(dcinside 단일) 기준 현재 시각도 정상 유입 중.
- 알림 발화: 06-01~06-01 = 0건, 06-02 22:29 첫 발화 5건 → 06-03 02-08시 304건 집중 → 누적 312건. 7일 중 사실상 25h 내 집중.
- LLM grounding: 0.3456 (06-01) → 0.3515 (06-02) → 0.3486 (06-03) — 변동 ±0.6%pt, 추세 평탄. P4.2 주장 0.68 대비 51% 수준.
- voc_records: P3.5 stabilized 시점 114k+ → 현재 (정확 수치 미산출, 7d 79,454 누적 가산 시 추정 180-200k 범위, 추정).

## 6. 다음 점검 시점

- **+5h (06-03 06:30 KST)**: 알림 cooldown 조치 후 발화 빈도 재측정 — 목표 1h ≤ 12건.
- **+8h (06-03 09:30 KST)**: daily insight·report 생성 확인 + grounding 0.35 갱신 확인.
- **+24h (06-04 01:25 KST)**: reddit/twitter 재기동 효과 — 24h 신규 ≥ 50건 목표. xda 18일 dead 종결 또는 비활성 처리 확인.
- **+7d (06-10)**: 6차원 재평가, degraded → healthy 전환 목표.
