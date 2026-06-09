# MCP Server

Claude가 SignalForge VOC 데이터베이스에 직접 질문하는 MCP 도구 서버.

Source: [[mcp-server/server.py#mcp]]

## Tools

7개 MCP 도구. Claude가 VOC 데이터를 분석할 때 호출한다.

### query_voc

제품 VOC를 조건별 조회. 가장 기본적인 조회 도구.

- `product_code` 필수. `country`, `category`, `sentiment` 선택 필터.
- `limit` 최대 100으로 제한.

Source: [[mcp-server/tools/query.py#query_voc_tool]]

### get_top_issues

지난 N일간 상위 이슈 랭킹. 카테고리별 건수 + 부정 비율 반환.

Source: [[mcp-server/tools/query.py#get_top_issues_tool]]

### search_voc

PostgreSQL FTS(Full-Text Search) 기반 키워드 검색.
`to_tsvector('english', content_translated) @@ plainto_tsquery` 사용.

Source: [[mcp-server/tools/query.py#search_voc_tool]]

### analyze_sentiment_trend

감성 점수 시계열. `granularity=week` 기본값. [[data-model#Tables]] 의 sentiment_score 컬럼 기반.

Source: [[mcp-server/tools/analytics.py#analyze_sentiment_trend_tool]]

### compare_products

여러 제품의 카테고리별 평균 감성 점수 비교. 레이더 차트 데이터 구조로 반환.

Source: [[mcp-server/tools/analytics.py#compare_products_tool]]

### get_country_breakdown

국가별 VOC 건수 + 긍정 비율 + 평균 감성. 세계 히트맵 데이터.

Source: [[mcp-server/tools/analytics.py#get_country_breakdown_tool]]

### get_voc_summary

주간 VOC 현황을 한 줄 텍스트로 요약. Claude가 이 텍스트를 받아 자체 분석 문장을 생성함.

Source: [[mcp-server/tools/analytics.py#get_voc_summary_tool]]

## Usage Example

Claude가 MCP 도구를 조합해 사용자 질문에 답하는 예시.

```text
사용자: "갤럭시 Z Fold7의 힌지 불만이 많아?"

Claude → MCP 호출:
  get_top_issues(product_code="GZF7", period_days=30)
  query_voc(product_code="GZF7", category="build_quality", sentiment="negative")

결과 해석 후 응답:
  "지난 30일간 Z Fold7 부정 VOC 중 38%가 힌지 관련입니다.
   주요 불만: 힌지 소음(42%), 주름(31%), 내구성 우려(27%)"
```

## DB Connection

Source: [[mcp-server/db.py#get_db_session]]

`async with get_db_session() as db:` 컨텍스트 매니저로 세션 관리.
`DATABASE_URL` 환경변수 필수.
