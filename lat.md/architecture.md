# Architecture

SignalForge는 4개 레이어로 구성된다.

## Layers

Crawler가 Raw VOC를 수집하여 NLP 파이프라인을 거쳐 PostgreSQL에 저장한다. FastAPI가 데이터를 Frontend과 MCP Server에 노출한다.

```
[Crawler Layer]  →  [NLP Pipeline]  →  [PostgreSQL]
                                             ↓
                                      [FastAPI Backend]
                                        ↓         ↓
                                 [Frontend]   [MCP Server]
                                 Dashboard    Claude Tools
```

## Crawler Layer

- `crawler/` 디렉토리가 독립 Docker 컨테이너로 실행된다.
- [[crawler#BaseCrawler]]가 모든 플랫폼 크롤러의 공통 로직을 담는다.
- Celery Worker가 크롤링을 실행하고, Celery Beat가 스케줄을 관리한다.
- Redis가 Celery 브로커 역할을 한다.

Source: [[crawler/celery_app.py#app]]

## NLP Pipeline

수집된 원시 텍스트를 표준 VOC로 변환하는 처리 흐름.
자세한 내용은 [[voc-pipeline]]과 [[nlp]] 참조.

## FastAPI Backend

- `backend/app/` 아래에 라우터·서비스·모델이 위치한다.
- async SQLAlchemy 2.0 + asyncpg로 PostgreSQL에 연결한다.
- WebSocket `/ws/realtime`으로 신규 VOC를 실시간 브로드캐스트한다.

Source: [[backend/app/main.py#app]]

## MCP Server

- `mcp-server/` 컨테이너가 FastMCP로 7개 도구를 노출한다.
- Claude Desktop 또는 Claude API가 이 도구를 호출해 VOC 데이터를 분석한다.

Source: [[mcp-server/server.py#mcp]]

## Frontend

React 18 + TypeScript + Vite + Ant Design 5 기반 대시보드. `frontend/src/` 아래에 위치.

- React 18 + TypeScript + Vite + Ant Design 5
- Zustand로 필터 전역 상태 관리, TanStack Query v5로 API 캐싱
- 경로: `frontend/src/`
