# SignalForge MCP 서버

Samsung Galaxy VOC 데이터베이스에 자연어로 질문하는 MCP 서버. FastMCP `streamable-http`.

> **먼저 읽을 것 — 이 :8013 은 상위 게이트웨이의 백엔드 다리 하나다.**
> 에이전트는 이 포트를 **직접 안 친다**. 인증·토큰 발급·HTTP 노출은 상위 **HWAXMcpGateway(:9110)** 가 담당한다.
> `:8013` 만 보고 "정적 토큰 하나뿐, 발급 플로우 없음" 이라 결론내면 **틀린다**. 아래 2계층 구조를 보라.

---

## 2계층 구조 (토큰 발급은 상위 게이트웨이가 함)

```
                          에이전트  (mcp_servers.json 엔트리 1개)
                                    │  Authorization: Bearer <GW_TOKEN>   ← 포털 발급 토큰
                                    ▼
        ┌─────────────────────────────────────────────────────┐
        │  HWAXMcpGateway   gateway.py   127.0.0.1:9110/mcp     │  ← 상위 게이트웨이
        │  · _bearer_gate 인바운드 인증 (GW_TOKEN 검증)          │
        │  · 3개 MCP tool 을 union → 재노출 (SF 22개 포함)       │
        │  · allowed_groups 그룹 가시성 + audit                  │
        │  · 백엔드별 네이티브 토큰 주입 (토큰 교환)             │
        └───────┬──────────────┬───────────────┬───────────────┘
   inject Bearer sfmcp_…   Bearer rat_…    Bearer mxwp_…
                │              │               │
         ┌──────▼─────┐ ┌──────▼──────┐ ┌──────▼────────┐
         │ signalforge│ │reportarchive│ │ mx-white-paper│
         │  이 서버   │ │   :3002     │ │    :8765      │
         │   :8013    │ └─────────────┘ └───────────────┘
         └────────────┘
```

**핵심 패턴 — "호출자 토큰 1개 → 백엔드별 네이티브 토큰 주입".**
- 에이전트는 **포털이 발급한 토큰 하나**(`GW_TOKEN`)로 게이트웨이 `:9110` 만 호출한다.
- 게이트웨이가 각 백엔드로 fan-out 하며 그 서비스 전용 토큰을 주입한다. SignalForge 는 `Authorization: Bearer sfmcp_…` (= 이 서버의 `SF_MCP_TOKEN`).
- 따라서 에이전트는 SF 의 정적 토큰을 **알 필요도 없고** :8013 을 직접 접속하지도 않는다.
- 그룹 권한(`allowed_groups`)·감사(audit)·REST 확장(`rest_proxy.py`, SF 는 `X-API-Key` 주입)도 전부 게이트웨이 계층.

**게이트웨이 config**: `~/claude/HWAXMcpGateway/gateway_config.json` (라이브) — signalforge 엔트리 `url: http://127.0.0.1:8013/mcp`, 주입 헤더 `Bearer sfmcp_…`.
**게이트웨이 코드**: `~/claude/HWAXMcpGateway/gateway.py` (`_aggregate()` 가 기동 시 3개 백엔드 tool 을 수집).
**포털 등록**: `HWAXPortal/backend/app/mcp/`(registry·routes — 메타데이터+그룹가시성) · `infra/services.yaml`(mcp-gateway tier16).

---

## 이 서버 단독 (:8013)

- **transport**: `streamable-http`, 엔드포인트 `http://127.0.0.1:8013/mcp` (loopback 전용, 외부 노출 없음).
- **포트**: `MCP_PORT` env. 기본 8001 이지만 이 호스트는 AIDataHub 가 8001 점유 → **8013**.
- **인증(단독 기동 시)**: `.env` 의 `SF_MCP_TOKEN` 이 있으면 순수 ASGI `_BearerGate` 가 모든 요청의 `Authorization: Bearer <token>` 검증(틀리면 401). 없으면 무인증 standalone. **정적 pre-shared 토큰이며 발급/회전 없음** — 발급은 상위 게이트웨이 몫.
- **기동**: `./mcp-server/start_mcp.sh` (컨테이너로는 `sf-mcp` instance, `scripts/up.sh` 가 관리).
- **도구 수**: 22개 (`@mcp.tool` — server.py). 조회 7 · 운영/브리핑 4 · 차트 5 · 차원분석 6.

---

## 스키마 함정 (raw SQL 짤 때 주의 — 도구 쓰면 안 만남)

MCP 도구를 쓰면 아래가 전부 가려지지만, DB 를 직접 건드릴 땐 반드시 걸린다.

| 함정 | 정본 | 낡은/오해 |
|---|---|---|
| **카테고리 컬럼** | `voc_records.categories` (라이브, 수집 즉시 채워짐) | `voc_records.topics` = **낡은 배치**. 최근 30일 **0건** — 최근 데이터에 blind. MCP 도구는 전부 `categories` 사용. |
| 제품 연결 | `voc_records.product_id` → `products.id` (`products.code`, `name_ko`) | `product_code` 컬럼은 **없다**. |
| 카테고리 기간 | 차트류는 `published_at` 기준 | "최근 수집" 은 `collected_at` 기준. 목적에 맞게 선택. |
| 레코드 테이블 | `voc_records` (활성 뷰 `voc_active`) | `voc_entries` 아님. |

**도구 파라미터 워트**: `search_voc` 는 검색어를 `query` 가 아니라 **`keyword`** 로 받는다. `get_voc_summary`·`get_top_issues` 는 `product_code` 필수.

---

## psql 직접 접속 (컨테이너)

`apptainer exec` 는 instance 의 `--env` 를 상속 안 함 → 접속 정보를 직접 넘긴다.

```bash
apptainer exec --env PGPASSWORD=signalforge_pass instance://sf_postgres \
  psql -h 127.0.0.1 -p 5434 -U signalforge -d signalforge -tAc "SELECT count(*) FROM voc_records"
```
