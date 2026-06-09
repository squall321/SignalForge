# P3 통합 결과 보고서 (T3 커뮤니티 + T4 국가)

작성일: 2026-06-02
범위: P3-1 ~ P3-5 (5 트랙)
상태: P3-1 / P3-5 완료, P3-2 / P3-3 / P3-4 Rate Limit 으로 중단

---

## 1. 트랙별 결과 표

| 트랙 | 산출 | 검증 수치 | 상태 |
|------|------|-----------|------|
| P3-1 DB MV | `0004_p3_objects.py` (platform_health + country_daily), Celery `run_refresh_p3_mvs`, Beat `*/30` | platform_health 72행 (active 57 / idle 7 / dead 8), country_daily 1016행 / 27국 / 14일, REFRESH CONCURRENTLY 137+78 = 215ms, pytest 2 passed | 완료 |
| P3-2 T3 Backend | 6 endpoint (heatmap / dispersion / cluster / site_detail / cross_site / leaderboard) | — | 중단 (Rate Limit) |
| P3-3 T3 Frontend | Heatmap + Dispersion + Cluster 페이지 | — | 중단 (Rate Limit) |
| P3-4 T4 Backend | 4 endpoint (country_map / drilldown / diffusion / product_compare) | — | 중단 (Rate Limit), 1/4 (country_map) 만 가동 추정 |
| P3-5 T4 Frontend | `/geo` 페이지, WorldChoropleth + Drilldown + DiffusionPlayer + ProductCompareBar, lazy 라우트 | vitest 6 passed, build 성공, GeoView 청크 154KB / gzip 55.95KB | 완료 (3 endpoint fallback) |

---

## 2. 가동 절차

```bash
# 1) DB 마이그레이션 (P3-1 MV 생성)
cd /home/koopark/claude/SignalForge/backend
alembic upgrade head        # 0004 (head) 확인

# 2) 백엔드 재시작 (FastAPI + Celery worker + Beat)
systemctl --user restart signalforge-api
systemctl --user restart signalforge-celery
systemctl --user restart signalforge-beat   # */30 refresh-p3-mvs 등록 확인

# 3) 프론트엔드 개발 서버
cd /home/koopark/claude/SignalForge/frontend
npm install                 # react-simple-maps, d3-scale 신규 의존성
npm run dev

# 4) 접속
#  - /community  (P3-3 미배포 → 404 또는 placeholder)
#  - /geo        (P3-5 완료, 3 endpoint fallback 더미)
```

---

## 3. 검증 (curl + npm)

### 3.1 Community 6 endpoint (P3-2 미배포 → 전부 미검증)
```bash
curl -s :8000/api/community/heatmap?days=14 | jq .         # 예정
curl -s :8000/api/community/dispersion?product=galaxy-s25  # 예정
curl -s :8000/api/community/cluster?days=7                 # 예정
curl -s :8000/api/community/site/dcinside                  # 예정
curl -s :8000/api/community/cross_site?product=iphone-17   # 예정
curl -s :8000/api/community/leaderboard?days=7             # 예정
```
결과: 0 / 6 동작 (P3-2 미배포).

### 3.2 Geo 4 endpoint (P3-4 부분 / fallback)
```bash
curl -s :8000/api/geo/country_map?days=14 | jq '.[] | length'      # 1016
curl -s :8000/api/geo/country/KR/drilldown   # 404 (미배포, frontend fallback)
curl -s :8000/api/geo/country_diffusion?product=galaxy-s25  # 404 (fallback)
curl -s :8000/api/geo/product/galaxy-s25/country_compare    # 404 (fallback)
```
결과: 1 / 4 정상 (country_map), 3 / 4 프론트 fallback 더미로 UI 동작.

### 3.3 페이지 검증
```bash
cd /home/koopark/claude/SignalForge/frontend
npx vitest run               # 6 passed (color scale 3 / indexByCountry 1 / ProductCompareBar 2)
npm run build                # 성공, GeoView 154KB (gzip 55.95KB)
```
- `/community` : 미배포 → 빈 placeholder.
- `/geo` : 지도 + 드릴다운 + 확산 플레이어 + 비교 막대 동작 (3 endpoint fallback 더미).

---

## 4. P4 진입 전 사용자 결정

**옵션 A — 신규 기능 (2주)**
- 실시간 WebSocket 스트리밍 (신호 push)
- LLM Narrative 자동 생성 (일간 요약)
- Slack 알림 (임계치 트리거)

**옵션 B — 운영 안정화 (권장)**
- P3-2 / P3-3 / P3-4 Rate Limit 미완료 트랙 재개 (커뮤니티 6 endpoint + 프론트, 국가 3 endpoint)
- P2 `run_refresh_p2_mvs` Celery 워커 psycopg2 누락 수정 (P3-1 작업 중 발견)
- platform_health dead 8개 사이트 크롤러 점검

→ **결정 필요**: A 진행 / B 안정화 / A+B 동시 (인력 분리).

---

## 5. 리스크

| 항목 | 영향 | 대응 |
|------|------|------|
| 번들 크기 | GeoView 154KB / gzip 55.95KB 추가. 전체 초기 로드는 lazy 라우트로 격리됨 | 현 상태 허용, 추가 페이지마다 lazy 강제 |
| 지도 라이브러리 | react-simple-maps + jsDelivr TopoJSON CDN 의존, 오프라인/사내망 실패 가능 | TopoJSON 정적 호스팅 백업 검토 |
| MV refresh 부하 | P3-1 두 MV 합계 215ms / 30분 주기, 여유 충분. 단 P1+P2+P3 MV 누적 시 동시 락 가능성 | 스케줄 분산 (P1=15분, P2=20분, P3=30분 어긋남 유지) |
| 클러스터 안정성 | P3-2 미배포로 cluster endpoint 미검증, 알고리즘(HDBSCAN 등) 파라미터 튜닝 미실시 | P3-2 재개 시 silhouette / noise 비율 모니터링 필수 |
| psycopg2 누락 | P2 refresh Celery 태스크 실 워커 실행 시 ImportError 위험 (P3-1 동일 패턴은 subprocess psql 로 회피) | 워커 이미지에 psycopg2-binary 추가 또는 P2 도 subprocess 패턴 통일 |
