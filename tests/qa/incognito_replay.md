# URL 공유 재현 (Incognito Replay) QA 가이드

## 목적

대시보드의 **공유 가능한 URL** (필터·기간·제품 코드 등이 query string 으로 인코딩됨)
이 다른 브라우저 세션에서 동일한 데이터를 재현하는지 검증한다.

세션-종속 상태 (사이드바 접힘, localStorage 캐시 등) 가 데이터에 영향을 주지
않아야 한다는 회귀 방지 테스트.

## 사전 조건

- 백엔드 (`http://127.0.0.1:8000`), 프론트 (`http://127.0.0.1:5173` 또는 Nginx) 가동
- 두 브라우저 프로파일:
  1. **주 세션** — 로그인된 일반 브라우저
  2. **시크릿 세션** — incognito / private mode

## 절차

### 1. URL 캡처

주 세션에서 대시보드를 열고 다음 조작을 수행한다.

1. 제품 = `GS25U`
2. 기간 = `30d`
3. Category 필터 = `camera, battery`
4. 정렬 = `count desc`

URL bar 의 전체 query string 을 복사한다. 예:

```
http://localhost:5173/dashboard?product=GS25U&period=30d&categories=camera,battery&sort=count_desc
```

### 2. 응답 스냅샷 저장

같은 브라우저의 DevTools → Network 탭에서 다음 API 응답을 JSON 으로 export:

```
GET /api/v1/analytics/category-dist?product=GS25U&period_days=30
GET /api/v1/analytics/top-issues?product=GS25U&period_days=30&top_n=10
GET /api/v1/analytics/sentiment-trend?product=GS25U&period_days=30&granularity=day
```

각각 `tests/qa/snapshots/<endpoint>__main.json` 로 저장.

### 3. Incognito 에서 동일 URL 재생

1. 시크릿 창을 열고 1 번에서 복사한 URL 을 붙여넣어 진입
2. DevTools → Network 탭에서 동일한 3 개 API 응답을 export
3. `tests/qa/snapshots/<endpoint>__incognito.json` 로 저장

### 4. Diff 검증

```bash
cd tests/qa/snapshots
for ep in category-dist top-issues sentiment-trend; do
  echo "── $ep ──"
  diff <(jq -S . ${ep}__main.json) <(jq -S . ${ep}__incognito.json) && echo OK
done
```

기대 결과: **모든 endpoint 의 diff 가 빈 출력** (= 동일).

### 5. 허용 가능한 차이

다음 필드는 timestamp 성격이라 diff 에서 제외 (`jq 'del(...)'`).

- `generated_at`
- `cache_key`
- `request_id`

## 실패 시 대응

- query string 의 일부가 React state 로 들어가지 않고 localStorage 만 의존하는
  경우 → frontend 라우팅 hook 점검 (`useSearchParams` 로 단일 출처)
- API 응답이 다르다면 → 백엔드 캐시 TTL / per-session 캐시 키 검사

## CI 자동화 (옵션)

```bash
# 헤드리스 chromium 으로 URL 두 번 (정상/incognito) 방문, network capture diff
node tests/qa/replay_runner.js \
  --url "$SHARED_URL" \
  --out tests/qa/snapshots/
```

`replay_runner.js` 는 Playwright 의 `browser.newContext({ storageState: ... })`
및 `browser.newContext()` (no state) 두 컨텍스트를 사용해 정확히 같은 라우트를
방문하고 network 응답을 diff 한다.
