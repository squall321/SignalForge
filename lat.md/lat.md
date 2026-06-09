This directory defines the high-level concepts, business logic, and architecture of this project using markdown. It is managed by [lat.md](https://www.npmjs.com/package/lat.md) — a tool that anchors source code to these definitions. Install the `lat` command with `npm i -g lat.md` and run `lat --help`.

- [[index]] — 프로젝트 개요 및 섹션 목록
- [[architecture]] — 전체 시스템 구성 및 레이어 관계
- [[data-model]] — 핵심 데이터 스키마 및 ERD
- [[voc-pipeline]] — VOC 수집·처리·저장 전체 흐름
- [[crawler]] — 플랫폼별 크롤링 전략
- [[api]] — FastAPI 백엔드 엔드포인트 명세
- [[nlp]] — 언어감지·번역·감성분석·카테고리분류
- [[mcp-server]] — MCP 도구 서버 (Claude ↔ VOC DB)
- [[products]] — 대상 제품군 코드 체계
- [[categories]] — VOC 카테고리 분류 표준
- [[infra]] — Docker Compose 및 환경변수 설계
