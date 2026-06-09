# SignalForge

Samsung MobileExperience VOC(Voice of Customer) 인텔리전스 플랫폼.
7개 글로벌 플랫폼에서 VOC를 자동 수집·분류·분석하고, LLM이 MCP를 통해 데이터에 직접 질문하여 개발 개선 방향을 도출한다.

## Sections

각 스펙 파일은 코드베이스의 핵심 개념과 비즈니스 로직을 정의한다.

- [[architecture]] — 전체 시스템 구성 및 레이어 관계
- [[data-model]] — 핵심 데이터 스키마 및 ERD
- [[voc-pipeline]] — 수집 → NLP 처리 → 저장 전체 흐름
- [[crawler]] — 플랫폼별 크롤링 전략과 봇 감지 우회
- [[api]] — FastAPI 백엔드 엔드포인트 명세
- [[nlp]] — 언어감지·번역·감성분석·카테고리분류 로직
- [[mcp-server]] — Claude가 VOC 데이터에 질문하는 MCP 도구
- [[products]] — 대상 제품군 코드 및 분류 체계
- [[categories]] — VOC 카테고리 분류 코드 표준
- [[infra]] — Docker Compose, Redis, 환경변수 설계
