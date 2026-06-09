# SignalForge Frontend (P1 MVP)

Samsung MX VOC Intelligence Platform — React + Vite + TypeScript + AntD 5.

## Stack

- React 18 + TypeScript
- Vite 5 (dev: 5174, proxy `/api` -> FastAPI 8000)
- Ant Design 5 (한국어 locale)
- Zustand (전역 필터 store)
- React Router v6 + URL <-> store 양방향 동기화
- TanStack Query v5
- Axios

## 디렉토리

```text
src/
  components/layout/  AppLayout, GlobalFilterBar
  hooks/              useFilterUrlSync
  pages/              Dashboard (Overview KPI placeholder)
  services/           api (axios)
  stores/             useFilterStore (zustand)
  types/              filters
  App.tsx, main.tsx, index.css
```

## 개발

```bash
cd /home/koopark/claude/SignalForge/frontend
npm install
npm run dev          # http://127.0.0.1:5174
```

빌드:

```bash
npm run build && npm run preview
```

## 백엔드 연결

- Vite dev proxy: `/api/*` -> `http://127.0.0.1:8000/api/*`
- 절대 URL을 쓰려면 `.env.local` 에 `VITE_API_BASE_URL=http://...` 지정.

## 필터 URL 동기화

- 헤더의 RangePicker / Select 변경 → URL 쿼리스트링 자동 갱신 (history.replace)
- 새 탭/incognito 에서 동일 URL을 열면 동일 필터로 복원됨
- 예: `http://127.0.0.1:5174/dashboard?start=2025-05-01&end=2025-05-31&products=GS25,GZF6&regions=NA,KR&platforms=reddit,youtube`

## 다음 단계 (P1-2 이후)

- /api/v1/products, /platforms 호출하여 필터 옵션 동적 로드
- KPI 카드 → /api/v1/analytics/* 연동
- Trends / Alerts / Insights 페이지 추가
