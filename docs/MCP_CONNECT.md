# SignalForge MCP 서버 연결 가이드

`mcp-server/` 는 FastMCP(Streamable HTTP) 기반으로 SignalForge VOC 데이터베이스(11만+행)에 직접 질의할 수 있는 도구 11종을 노출합니다.

---

## 1. 노출 도구 (총 11종)

### VOC 조회 (3)
| 도구 | 용도 |
| --- | --- |
| `query_voc(product_code, country?, category?, sentiment?, limit?)` | 조건별 VOC 레코드 조회 |
| `get_top_issues(product_code, period_days?, top_n?)` | 기간 내 카테고리 빈도 TOP |
| `search_voc(keyword, product_code?, limit?)` | FTS 전문 검색 (영문 권장) |

### 분석 (4)
| 도구 | 용도 |
| --- | --- |
| `analyze_sentiment_trend(product_code, period_days?, granularity?)` | 감성 시계열 |
| `compare_products(product_codes[], category?)` | 다중 제품 카테고리 비교 |
| `get_country_breakdown(product_code, period_days?)` | 국가별 분포 |
| `get_voc_summary(product_code, period_days?)` | 요약 텍스트 |

### 인사이트/운영 (4 — 신규)
| 도구 | 용도 |
| --- | --- |
| `daily_briefing(date?)` | 지정 일자(KST) 자연어 일일 브리핑 |
| `alert_check()` | 임계치(부정비율·부정급증·수집정체) 상태 요약 |
| `site_health()` | 플랫폼별 24h 활동 현황 + 상태(healthy/quiet/stale) |
| `top_emerging_keywords(period_days?, product_code?, top_n?)` | 한국어/영어 키워드 토큰 빈도 |

---

## 2. 서버 기동

```bash
cd /home/koopark/claude/SignalForge/mcp-server
source .venv/bin/activate
# DATABASE_URL 은 .env 또는 환경변수로
export DATABASE_URL='postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge'
export MCP_PORT=8002    # 기본 8001, 운영은 8002
python server.py
```

엔드포인트: `http://127.0.0.1:8002/mcp/` (FastMCP streamable-http)

---

## 3. Claude Desktop 연결

`~/.config/Claude/claude_desktop_config.json` (Linux) 또는
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "signalforge": {
      "command": "/home/koopark/claude/SignalForge/mcp-server/.venv/bin/python",
      "args": ["/home/koopark/claude/SignalForge/mcp-server/server.py"],
      "env": {
        "DATABASE_URL": "postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge",
        "MCP_PORT": "8002"
      }
    }
  }
}
```

원격 HTTP 모드를 쓰는 경우 (서버를 이미 띄워둔 상태):

```json
{
  "mcpServers": {
    "signalforge": {
      "transport": "streamable-http",
      "url": "http://127.0.0.1:8002/mcp/"
    }
  }
}
```

Claude Desktop 을 재시작하면 도구 목록에서 `signalforge` 네임스페이스가 보입니다.

---

## 4. claude.ai 웹에서 사용

claude.ai 의 **Connectors** (또는 Custom Integrations) 에서 Streamable HTTP 커넥터로 등록 가능합니다. URL 만 입력:

```
https://<your-host>/mcp/
```

(로컬 테스트는 `ngrok http 8002` 또는 Cloudflare Tunnel 로 노출 후 등록)

---

## 5. 예시 질의 (Claude 에 그대로 입력)

1. **Z Fold7 이슈 톱5는?**
   → Claude 가 `get_top_issues(product_code="GZF7", period_days=30, top_n=5)` 호출 → 카테고리별 부정 비율 포함.

2. **갤럭시 S25 울트라 vs iPhone 사용자 의견 비교** (제품 등록되어 있다면)
   → `compare_products(["GS25U", "IP16P"])` → 카테고리별 감성 표.

3. **최근 한 달 카메라(camera) 카테고리 신흥 키워드 TOP 20 (한국어)**
   → `top_emerging_keywords(period_days=30)` 후 Claude 가 한국어 토큰 필터링.

4. **오늘 일일 브리핑 줘**
   → `daily_briefing()` → 자연어 마크다운 브리핑.

5. **지금 알람 걸린 거 있어? 어떤 사이트 멈춰 있어?**
   → `alert_check()` + `site_health()` 병렬 호출 → 부정 급증·정체 플랫폼 요약.

---

## 6. 임계치 (alert_check) 기본값

| 항목 | 값 |
| --- | --- |
| 부정 비율 경보 | ≥ 40% (24h 30건 이상 시) |
| 부정 급증 | 24h 부정 ≥ 직전 24h × 2.0 |
| 플랫폼 정체 | 마지막 수집 ≥ 12시간 |

조정이 필요하면 `mcp-server/tools/insights.py` 상단 상수 (`NEG_RATIO_THRESHOLD`, `MIN_VOLUME`, `NEG_SURGE_RATIO`) 를 수정하거나 `.env` 화 합니다.

---

## 7. 재시작 후 도구 목록 확인

서버를 띄운 뒤:

```bash
cd /home/koopark/claude/SignalForge/mcp-server
.venv/bin/python -c "
import asyncio, json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client('http://127.0.0.1:8002/mcp/') as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print(json.dumps([t.name for t in tools.tools], indent=2))

asyncio.run(main())
"
```

11개 도구 (기존 7 + 신규 4) 가 모두 출력되어야 합니다.

---

## 8. 그래프 규격 도구 (2026-06-12 신규)

기존 11개 도구 + **차트 4종** = 15개. 모든 차트 도구는 `voc_active` 기반
(archived 제외 → backend `/data-quality` 와 수치 정합) 으로, 표준 스키마를 반환:

```json
{ "chart_type": "line|bar", "raw": {...}, "echarts_option": {...}, "summary": "..." }
```

- `raw` — 원본 집계 (다른 라이브러리 매핑용)
- `echarts_option` — chartTheme.ts 규격 ECharts option (Okabe-Ito 8색). 그대로 렌더 가능
  - ⚠️ 한국어 tooltip formatter 는 JS 함수라 직렬화 불가 → `tooltip.trigger` 만 포함.
    frontend 렌더 시 `chartTheme.ts` 의 formatter 를 주입하면 됨

### 도구

| 도구 | 인자 | 차트 | 용도 |
|---|---|---|---|
| `chart_sentiment_timeseries` | `product_codes:[]`, `days`, `granularity` | 다제품 라인 | 제품별 VOC 추세 비교 |
| `chart_country_distribution` | `product_code?`, `top_n` | 가로 막대 | 국가별 분포 |
| `chart_category_distribution` | `product_code?`, `top_n` | 가로 막대 | 카테고리별 분포 |
| `chart_crisis_timeline` | `case_code?` | line+area | 위기 사례 timeline (peak marker). code: GN7/GZF1/GS22U/GZFL3/GS20 |

### LLM 사용 흐름

1. 도구 호출 → `echarts_option` 수령
2. 그대로 `<ReactECharts option={echarts_option}/>` 또는 ECharts setOption 에 전달
3. 또는 `raw` 를 받아 다른 차트 라이브러리 (Vega/matplotlib) 로 매핑

### Tier 2 (예정)

- `chart_keyword_network` — 키워드 동시출현 force-graph (deep_service.keyword_network 차용, 성능 검증 분리)
