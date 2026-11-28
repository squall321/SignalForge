# MCP 현대화 — stateless · 공통 계약 (2026-11-28)

## 계획

포털 hwaxportal-71 이 사용자 지시로 모든 MCP 를 stateless 와 공통 계약(포털 `docs/mcp-modernize/contract.md`)에 맞추고 있다.
SignalForge 몫은 signalforge-7f 에 갔지만 7f 가 보안 결정 여섯의 답을 기다리며 멈춰, 71 이 c6 에 맡을 수 있으면 하라고 했다
(7f 에는 결정이 이미 적용됐고 c6 이 맡는다고 알렸다 — 7f 의 가지 · worktree 는 없었다).

1. **P1 stateless(1단계)** — `_MaskedFastMCP(..., stateless_http=True)`. SDK 1.27.1 이 받는다. 도구는 DB 조회뿐이라 세션 상태가 없다.
2. **C2 인자 설명** — 등록 길목(`_MaskedFastMCP.tool`)에서 docstring 「Args:」 절을 떼어 인자마다 `Annotated[타입, Field(description=…)]`
   로 서명에 붙인다(도구 37 · 인자 93). 도구 설명에는 그 절을 뺀 글을 넘긴다.
3. **C6 표지** — 37개 모두 읽기 전용(INSERT · UPDATE · DELETE · commit 없음 — 확인함) → 기본 `ToolAnnotations(readOnlyHint=True,
   openWorldHint=False)`(camelCase). tools/list JSON 의 열쇠 꼴을 단언하는 시험.
4. 나머지 계약 항목(C1 · C3 · C4 · C5 · C7 · C8 · C9 · P2 · P3 · R1 ~ R3)은 상태만 답한다 — 이번 판은 포털이 짚은 셋.

## 체크리스트

- [x] 1 — stateless_http=True
- [x] 2 — Args → Field(description) 헬퍼 · 설명에서 Args 절 빼기 · 공통 필터 설명표 · 아홉 도구 Args 보탬(인자 156/156)
- [x] 3 — 기본 표지(readOnlyHint · openWorldHint) — 37/37
- [x] 시험 — tools/list JSON(camelCase 열쇠 · 인자 설명 100% · 설명에 'Args:' 없음 · stateless 설정) · Args 파서
- [ ] MCP 시험 · dev sf-mcp 다시 띄우기 · 살아 있는 서버 확인(stateless 라 세션 없이)
- [ ] 커밋 · main · push · 포털에 항목별 한 줄과 판
