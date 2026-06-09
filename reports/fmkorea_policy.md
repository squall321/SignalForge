# FMKorea 수집 정책 — Harvest 4 트랙 H2 (CASE B)

작성: 2026-06-06  /  라운드: harvest4  /  트랙: H2

## 1. Discovery 결론

- **Tor 미가동.** apptainer 컨테이너 호스트에 `tor` 데몬/설정 부재 (`which tor` → 비어있음, `systemctl status tor` → unit not found, 9050 포트 무응답).
- 설치는 `sudo apt install tor` 한 줄로 가능하나 호스트 root 권한 + systemd unit 운영 필요. 본 트랙 (Harvest 4) 범위 외.
- 결과: **CASE B 선택 — proxy_pool.py 인프라 유지, FMKOREA_USE_PROXY 미설정 (graceful 폴백).**

## 2. 현재 상태 (실측)

| 항목 | 값 | 출처 |
|---|---|---|
| platforms.fmkorea.is_active | `t` | `SELECT … FROM platforms` |
| 24h voc_records | 4 | `voc_records v JOIN platforms p` |
| 7d voc_records | 147 | 동일 |
| 직접 fetch 응답 | HTTP 429 (size 3198, 0.05s) | `curl -A Mozilla …` |
| FMKOREA_USE_PROXY env | 미설정 | `.env` |
| proxy_pool 단위 테스트 | 9/9 통과 (R3 Polish) | `test_proxy_pool.py` |
| H2 신규 단위 테스트 | 3/3 통과 | `test_fmkorea_tor.py` |

직접 fetch 가 HTTP 429 인 것은 IP rate-limit 신호이며 — 차단 (HTTP 430 / 보안 챌린지 페이지) 과는 다르다. 현재 Playwright 부트스트랩 경로는 정상 동작 중이며 24h=4 / 7d=147 수집은 보안 챌린지 통과 후 본문/댓글 수집을 의미한다.

## 3. CASE B 정책 (현 채택)

1. **활성 상태 유지.** `platforms.fmkorea.is_active = true` 변경 없음 — Playwright 경로가 일 평균 ~20건 (7d/7) 의 신호를 안정적으로 수집.
2. **proxy_pool 인프라는 코드만 보존.** `FMKOREA_USE_PROXY` 미설정 (또는 `false`) — `build_proxy_client_kwargs` 가 `{}` 반환, fmkorea.py 는 직접 호출 경로로 자동 폴백.
3. **단위 테스트 3종 신설** (`crawler/tests/test_fmkorea_tor.py`) — graceful 폴백 계약을 회귀 보호:
   - `test_fmkorea_graceful_fallback_when_tor_unreachable` — probe 실패 시 kwargs={}
   - `test_fmkorea_proxy_kwargs_spreadable_into_httpx_async_client` — `**kwargs` 펼침 무해
   - `test_fmkorea_module_imports_build_proxy_client_kwargs` — 소스 레벨에서 graceful 인터페이스 잔존 확인
4. **회복 대안 우선순위.**
   - (a) 보안 챌린지 통과 후 cookie/UA 재사용 캐시 수명 연장 — 24h 수집량 회복의 ROI 최고.
   - (b) GSMArena 갤럭시 user opinions HTML 파싱 (Discovery 권장) — 신규 소스, 글로벌 커버리지.
   - (c) Clien 회복 — KR 커뮤니티 대체.
   - (d) Tor 가동은 호스트 측 운영 결정 — 본 정책 범위 외.

## 4. CASE A (Tor 가용 시) 전환 방법

향후 호스트 측에서 `sudo apt install tor && sudo systemctl enable --now tor` 후:

```bash
export FMKOREA_USE_PROXY=true
# 기본 endpoint = socks5://127.0.0.1:9050 (proxy_pool DEFAULT_TOR_PROXY).
# 또는 명시: export FMKOREA_PROXY_URL=socks5://127.0.0.1:9050
```

이후 fmkorea.py 가 자동으로 SOCKS5 경로를 사용 (probe 성공 시) — 코드 변경 불필요.

## 5. 감사 추적

- 라운드: `harvest4` / 트랙: `H2_FMKorea_Tor_CaseB`
- 4중 안전장치 중 사용 항목: audit JSONL append (DRY_RUN/PRESERVE_EXISTING/ON CONFLICT 는 본 트랙 DB 변경 없음 → N/A).
- 산출 파일: `crawler/tests/test_fmkorea_tor.py`, `reports/fmkorea_policy.md`.

## 6. Self-report drift ±10%

- 보고 24h: 4 / 실측 24h: 4 (drift 0.0%)
- 보고 7d: 147 / 실측 7d: 147 (drift 0.0%)
- 보고 단위 테스트 추가: 3 / 실제 통과: 3 (drift 0.0%)
- 보고 회귀 (proxy_pool + tor): 12 / 실제 통과: 12 (drift 0.0%)
