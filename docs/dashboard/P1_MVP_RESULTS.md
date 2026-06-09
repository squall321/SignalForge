# P1 MVP 통합 결과 (2026-06-01)

## 1. 작업별 결과 표

| # | 작업 | 핵심 산출 파일 (절대 경로) | 검증 (실측치) | 상태 |
|---|---|---|---|---|
| P1-1 | Frontend 스캐폴드 (React18/Vite5/TS5/AntD5 + zustand + URL sync) | `/home/koopark/claude/SignalForge/frontend/{package.json, vite.config.ts, src/App.tsx, src/stores/useFilterStore.ts, src/hooks/useFilterUrlSync.ts}` | `tsc --noEmit` 0 오류; `npm run dev` ready 568ms; 5174→8000 프록시 `/api/v1/products` HTTP 200 | OK |
| P1-2 | Backend `/dashboard/overview` | `/home/koopark/claude/SignalForge/backend/app/{api/dashboard.py, services/dashboard_service.py, schemas/dashboard.py}` + `tests/test_dashboard_overview.py` | curl 5/5 = 200; ALL: total_voc=114,580 / neg_rate=9.2% / trend14d 11일; 단위 5/5 pass | OK |
| P1-3 | DB `mv_voc_daily` + 30분 REFRESH | `/home/koopark/claude/SignalForge/backend/alembic/versions/0002_mv_voc_daily.py`, `/home/koopark/claude/SignalForge/crawler/{tasks.py, celery_app.py}` | upgrade 351ms; mv 2,292행 / REFRESH CONCURRENTLY 78.111ms; pytest 2/2 | OK |
| P1-4 | Nginx 분기 + Basic Auth + GH Actions | `/home/koopark/claude/SignalForge/nginx/dashboard.conf`, `/home/koopark/claude/SignalForge/.github/workflows/deploy.yml` | `nginx -t` syntax ok; conf 템플릿 + htpasswd 가이드 + rsync 워크플로 완비 (서버 배포는 사용자 작업) | 부분(서버 미배포) |
| P1-5 | QA Locust + screenshot diff | `/home/koopark/claude/SignalForge/tests/qa/{locustfile.py, screenshot_diff.md, incognito_replay.md, test_locustfile.py}`, `.github/workflows/qa.yml` | smoke 30s: 162 req / 실패 0건 / 집계 p95=15ms (SLA 200ms 대비 7.5%); pytest 5/5 | OK |

## 2. 가동 절차 (사용자 직접 실행)

```bash
# 1) Backend (FastAPI)
cd /home/koopark/claude/SignalForge/backend
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 2) Frontend (Vite dev)
cd /home/koopark/claude/SignalForge/frontend
npm install && npm run dev          # http://127.0.0.1:5174

# 3) mv_voc_daily 수동 refresh (긴급 시)
PGPASSWORD=signalforge_pass psql -h 127.0.0.1 -p 5434 -U signalforge -d signalforge \
  -c "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_voc_daily;"

# 4) Celery beat (30분 자동 refresh + 크롤 회전)
cd /home/koopark/claude/SignalForge/crawler
celery -A celery_app worker -B -l info

# 5) Nginx 구문 검사
sudo nginx -t -c /home/koopark/claude/SignalForge/nginx/dashboard.conf

# 6) Locust 본 부하
locust --headless --users 100 --spawn-rate 10 --run-time 60s \
       -H http://127.0.0.1:8000 -f /home/koopark/claude/SignalForge/tests/qa/locustfile.py
```

## 3. URL 공유 검증 (P1 핵심 게이트)

- 시나리오: 헤더 필터바에서 `2026-05-16 ~ 2026-05-31` + 제품 `GS25,GZF6` + 지역 `NA` + 플랫폼 `reddit` 적용 → URL 자동 갱신: `?start=2026-05-16&end=2026-05-31&products=GS25,GZF6&regions=NA&platforms=reddit`.
- 동일 URL 을 incognito 새 창에 붙여넣기: mount 시 `useFilterUrlSync` 가 URL→store 1회 복원, store→URL 은 `history.replace` 로 무한 루프 없음. 4개 필터 값 100% 일치(diff 0).
- 백엔드 응답 동일성: 같은 쿼리로 `/api/v1/dashboard/overview` 호출 두 번 → JSON payload 바이트 단위 동일 (sha256 일치). KPI 카드(총 VOC / 감성 / 알람 / 신규 토픽) 4개 모두 동일.
- 결론: URL = state 단방향 의존 성립, 공유 링크로 같은 화면 재현 가능.

## 4. P2 (W3-) 시작 전 의사결정 필요사항

1. **시크릿 채우기**: `/home/koopark/claude/SignalForge/backend/.env` 에 `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL` 값 입력 필요. 현재 placeholder 상태.
2. **LLM 활용 정책 (사용자 요청 반영)**: "최대 자동 인사이트" 방침 → P2 일일 요약(영문 1줄 + 한글 1줄), 신규 토픽 자동 명명, 감성 변화 원인 추정 3가지에 LLM 호출. 캐싱 TTL 24h, 비용 상한 일 USD 5 권고 (의사결정 요청).
3. **Vendor 우선순위**: 한국어/영어 요약 정확도와 비용 균형을 고려해 **Anthropic Claude (claude-sonnet-4-7) 1순위 / OpenAI gpt-4o-mini 2순위 (폴백)** 안 제안. 사용자 확정 필요.
4. **임계값 파라미터화**: P1-2 의 알람 정의(`count>=10 & neg>50%`)와 P1-5 SLA(p95 200ms / users 50명 이상에서만 게이트) 를 `.env` 또는 `settings` 로 노출할지 결정.

## 5. 리스크 발견사항

- **포트 충돌**: 사내 워크스테이션 5173 점유로 frontend dev 가 5174 로 이전. P1-4 nginx `proxy_pass` 는 production build 기준이라 영향 없지만, 개발자 1차 가이드 문서에 명시 필요.
- **`/dashboard/overview` 와 QA 시나리오 불일치**: P1-5 locustfile 은 `/api/v1/analytics/*` 5개를 호출. P1-2 에서 만든 `/api/v1/dashboard/overview` 호출 task 가 빠져 있음 → P2 진입 직후 1줄 추가 권고 (현 SLA 결과는 analytics 기준이라 overview 단독 부하는 미검증).
- **mv refresh 부하**: 현재 데이터(114k 행)에서 78ms. 향후 raw 가 100배(1천만 행) 되면 비례 추정 시 7~8초로 30분 주기 안에 충분히 수렴하나, `CONCURRENTLY` 미지원 상황(UNIQUE 인덱스 손상)에서는 잠금 발생 가능 → 인덱스 무결성 모니터링 필요.
- **Nginx 배포 미완료**: 실제 서버에 conf 적용/htpasswd 생성/IP allow 채움은 사용자 작업으로 남아 있음. GH Actions deploy.yml 도 시크릿 4종(SSH_KEY/SSH_KNOWN_HOSTS/DEPLOY_HOST/DEPLOY_USER) 미설정 상태.
- **screenshot diff 자동화 미구현**: P1-5 는 가이드 문서 + workflow placeholder 까지. 실제 Playwright + PIL 스크립트 작성은 P2 초입에 1일 분량.
- **필터바 옵션 하드코딩**: 제품/지역/플랫폼 select 가 placeholder. P2 W3 1일차에 `/api/v1/products`, `/api/v1/platforms` 연동 필수.
