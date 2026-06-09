# T1. 지식 그래프 시각화 — 심층 설계

## 1. 목적 (Why)
SignalForge는 11만+ VOC를 product·category·platform 3축의 평면 집계로만 보여주고 있다. 하지만 실제 인사이트는 "Galaxy S26 - battery - Reddit 커뮤니티 - '발열' 키워드"처럼 **다축 동시 등장 패턴**에서 나온다. 지식 그래프는 이 다축 관계를 한 화면에 압축해, 다음 3가지 결정을 지원한다.

- **신호 발굴**: "어떤 제품-카테고리 조합이 어떤 사이트에서 부정 폭증 중인가" — edge weight + sentiment 색상으로 즉시 식별.
- **연관 키워드 추적**: "battery에 묶여 떠오르는 키워드가 '발열'에서 '수명'으로 옮겨갔다" — 키워드 노드의 시간 변화.
- **경쟁 매핑**: "iPhone 16 Pro Max와 동시 언급되는 키워드 vs Galaxy S26" — 두 제품 노드의 공통 이웃 비교.

평면 차트 5개를 봐도 안 보이는 "**관계의 형태**"가 핵심 가치다.

## 2. 데이터 모델

### 2.1 기존 컬럼 활용
- `voc_records.product_id, platform_id, categories[], content_translated, sentiment_score, published_at, country_code`

### 2.2 신규 테이블 (키워드 추출 결과 캐시)
```sql
CREATE TABLE voc_keywords (
  voc_id BIGINT REFERENCES voc_records(id) ON DELETE CASCADE,
  keyword TEXT NOT NULL,
  lang CHAR(2) NOT NULL,           -- 'ko' | 'en'
  position INT,                    -- 본문 내 시작 위치 (드릴다운 하이라이트용)
  PRIMARY KEY (voc_id, keyword, lang)
);
CREATE INDEX idx_voc_keywords_kw ON voc_keywords(keyword, lang);
CREATE INDEX idx_voc_keywords_voc ON voc_keywords(voc_id);
```

### 2.3 그래프 집계 Materialized View
```sql
CREATE MATERIALIZED VIEW mv_kg_edges_daily AS
SELECT
  date_trunc('day', v.published_at)::date AS bucket_day,
  'product'::text AS src_type, v.product_id::text AS src_id,
  'keyword'::text AS dst_type, k.keyword AS dst_id,
  COUNT(*) AS weight,
  AVG(v.sentiment_score) AS sentiment_avg,
  COUNT(*) FILTER (WHERE v.sentiment_label='negative') AS neg_n
FROM voc_records v
JOIN voc_keywords k ON k.voc_id = v.id
WHERE v.published_at >= now() - interval '180 days'
GROUP BY 1,2,3,4,5
HAVING COUNT(*) >= 3;
CREATE INDEX idx_mv_kg_edges_day ON mv_kg_edges_daily(bucket_day);
```
동일 패턴으로 `(category, keyword)`, `(platform, keyword)`, `(product, category)`, `(product, platform)` 5종 edge type 생성. Celery beat 1시간 주기 REFRESH.

### 2.4 키워드 추출 파이프라인
`crawler/nlp/keyword_extractor.py` 신규. **2단계 전략**:
- **MVP**: KoNLPy(Okt) for ko, simple noun chunker(POS=NN) for en, stopword 200개 + 길이≥2 + 문서 빈도 5회+. spaCy는 의존성 무거우므로 보류.
- **고도화 단계**: KeyBERT (sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) 로 의미 기반 top-5/VOC 추출 → 잡음 감소.

## 3. 시각화 명세

### 3.1 라이브러리 선택
| 후보 | 노드 한계 | 한글 라벨 | 시간축 | 결론 |
|---|---|---|---|---|
| cytoscape.js | 5k+ (압도) | 우수 | 직접 구현 | **채택** |
| d3-force | 1k 권장 | 우수 | 직접 구현 | 보조 (소규모 비교 뷰) |
| neovis.js | Neo4j 필수 | 보통 | 약함 | 기각 (Postgres 사용 중) |

cytoscape.js + `cola` layout + `cytoscape-popper` (tooltip) + `cytoscape-cxtmenu` (right-click 드릴다운).

### 3.2 노드/엣지 표현
- **노드 색상** = 엔티티 타입(product=#1677ff, category=#52c41a, platform=#fa8c16, keyword=#722ed1).
- **노드 테두리** = sentiment_avg gradient (red=−1.0 → gray=0 → green=+1.0), 두께 3px.
- **노드 크기** = log(VOC 빈도) × 8 + 12 (min 12, max 80).
- **엣지 두께** = log(edge weight) × 1.5.
- **엣지 색상** = neg 비율 ≥0.6 빨강, ≥0.3 주황, 그 외 회색.

### 3.3 ASCII Mockup
```
┌────────────────────────────────────────────────────────────────┐
│ [Period: 2026-03-01 ─────●────── 2026-06-01]  [TopN:80] [Apply]│
│ Filter: Product ▼  Category ▼  Region ▼   Layout: cola ▼       │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│        (Galaxy S26)━━━━━7━━━━━(battery)                        │
│             ┃                    ┃     ╲                       │
│             5                    9      ╲4                     │
│             ┃                    ┃       ╲                     │
│        (Reddit)─────3─────(camera)──6──(Pixel 9 Pro)           │
│             │                                                  │
│             4                                                  │
│             ↓                                                  │
│         (heat 발열)  ← 부정 86%, 노드 빨간 테두리              │
│                                                                │
├────────────────────────────────────────────────────────────────┤
│ ▶ Selected: keyword="발열"  | 132 VOC | neg 86% | 5 sample ↓   │
└────────────────────────────────────────────────────────────────┘
```

### 3.4 인터랙션
- **노드 클릭** → 우측 Drawer (440px) 슬라이드. 통계 + 샘플 VOC 5개(`content_translated` 우선, 키워드 하이라이트, sentiment 배지, 외부 링크).
- **노드 더블클릭** → 해당 노드를 중심으로 1-hop 서브그래프로 재로드.
- **상단 기간 slider** (Ant `Slider range`) → debounce 400ms 후 API 재호출, layout 부드럽게 transition (cytoscape `animate:true`).
- **edge hover** → 양 끝 노드 강조 + 동시 등장 VOC 수 툴팁.

## 4. API Endpoint 설계

### 4.1 그래프 조회
```
GET /api/v1/kg/graph
  ?start=2026-03-01&end=2026-06-01
  &edge_types=product-keyword,category-keyword
  &product_ids=12,15&platform_ids=&category_ids=
  &top_n=80&min_weight=3&lang=ko,en
```
응답:
```json
{
  "period": {"start": "2026-03-01", "end": "2026-06-01", "voc_count": 18420},
  "nodes": [
    {"id":"p:12","type":"product","label":"Galaxy S26",
     "size":62,"sentiment_avg":-0.18,"voc_count":3120},
    {"id":"k:발열","type":"keyword","label":"발열","lang":"ko",
     "size":28,"sentiment_avg":-0.71,"voc_count":132}
  ],
  "edges": [
    {"source":"p:12","target":"k:발열","weight":47,
     "neg_ratio":0.86,"sentiment_avg":-0.69}
  ],
  "stats":{"node_n":80,"edge_n":214,"truncated":true}
}
```

### 4.2 노드 드릴다운
```
GET /api/v1/kg/node/{node_id}/samples?start=&end=&limit=5
```
응답: VOC 배열 `[{id, product, platform, published_at, snippet_highlighted, sentiment_score, source_url}]`.

### 4.3 자동완성 (검색 박스)
`GET /api/v1/kg/search?q=배&limit=10` → keyword/product/category 통합 후보.

서버는 `mv_kg_edges_daily`에서 `bucket_day BETWEEN ...` 집계 후, weight desc로 TopN node 선별 → 그 노드들 사이 edge만 반환 (subgraph induction). p95 < 600ms 목표 (TopN=80 기준 측정).

## 5. 프론트 컴포넌트 구조 (React + Vite + AntD)
```
src/pages/KnowledgeGraph/
├── index.tsx                  // 페이지 컨테이너 + URL state ↔ filter 동기화
├── components/
│   ├── GraphCanvas.tsx        // cytoscape 인스턴스 lifecycle, layout, 이벤트
│   ├── GraphToolbar.tsx       // 기간 Slider, Filter Selects, TopN InputNumber
│   ├── NodeDetailDrawer.tsx   // 우측 드릴다운 Drawer
│   ├── EdgeTooltip.tsx        // 엣지 hover 카드
│   ├── LegendPanel.tsx        // 색상/크기 범례
│   └── EmptyState.tsx         // 데이터 부족 안내
├── hooks/
│   ├── useGraphData.ts        // SWR + abort controller
│   ├── useCytoscape.ts        // cy ref + style 정의 + 이벤트 바인딩
│   └── useUrlFilter.ts        // queryString sync
└── styles.ts                  // 색상 토큰
```
상태는 URL ↔ Zustand 한 store. 차트 데이터는 SWR 캐시. cytoscape는 `useRef`로 격리하고 React 재렌더 시 element diff만 적용 (전체 destroy 금지).

## 6. 인터랙션 흐름
1. 진입 시 default: 최근 30일, edge type `product-keyword + category-keyword`, TopN=60. URL 비어있으면 default 주입.
2. 사용자가 Slider로 90일로 확장 → 400ms debounce → `/kg/graph` 재호출 → cy.elements diff 후 cola re-layout.
3. 사용자가 "발열" 노드 클릭 → Drawer 열림 → `/kg/node/k:발열/samples` 호출 → 샘플 5개 + "이 키워드로 필터" 버튼.
4. "이 키워드로 필터" 클릭 → 메인 대시보드 `/dashboard?keyword=발열&start=...` 로 이동 (트랙 간 링크).
5. Drawer 내부 product 클릭 → 그래프에 해당 product 노드 highlight + 1-hop fit.

## 7. 단계적 구현

### Phase 1 — MVP (1주)
- `voc_keywords` 테이블 + KoNLPy/POS 기반 추출 배치 (1회 전수 + 신규 incremental).
- edge type 2종(`product-keyword`, `category-keyword`)만.
- `/kg/graph`, `/kg/node/{id}/samples` API.
- 정적 기간(최근 30일), TopN 고정 60. cytoscape default layout.

### Phase 2 — 강화 (1주)
- 기간 Slider + URL sync + 5종 edge type 전부.
- 노드 색상/크기/테두리 매핑 완성, legend.
- mv 1시간 REFRESH + Redis 캐시 layer (key = filter hash, TTL 10min).
- 자동완성 + 노드 더블클릭 1-hop 줌.

### Phase 3 — 고도화 (1.5주)
- KeyBERT 임베딩 기반 키워드 재추출 → 동의어 클러스터링(예: "발열"+"heat"+"hot" → cluster_id).
- 시간 애니메이션 (재생 버튼: 일자별 frame 30fps), 키워드 노드 등장/소멸 transition.
- 두 제품 비교 모드: 공통 이웃 강조, 차집합 dim.
- Export: PNG + JSON.

## 8. 트레이드오프와 한계
- **키워드 노이즈**: POS 기반은 "제품명, 일반 명사, 형태소 조각" 혼재. stopword + DF≥5 + 길이≥2로 1차 차단해도 잡음 잔존. → Phase 3 KeyBERT로 완화하되 비용/지연 트레이드오프.
- **그래프 가독성**: 노드 100개 넘어가면 hairball. TopN 80, min_weight 3 강제. 사용자가 늘리려 해도 200 cap.
- **시간축 비용**: Slider 매 이동마다 재집계는 부담. mv 일자 버킷 + 서버 메모리 집계로 회피, 그래도 90일 / 5 edge type 동시 = 약 200ms 집계.
- **다국어**: ko/en 외 언어는 11만 중 ~7%. 1차에서 ko+en만, 그 외 `language_detected` 표시만.
- **카테고리는 12개 고정**이라 keyword와 정보량 중복 — `category-keyword` edge는 의미 있으나 `product-category`는 그래프보다 막대차트가 우월. 그래프에선 보조 역할.

## 9. 검증 기준
1. **재현성**: 동일 필터로 두 번 호출 시 노드/엣지 집합 완전 동일 (deterministic ordering, tie-break = label asc).
2. **성능**: TopN=80, 기간 90일 기준 API p95 < 600ms, 클라이언트 first paint < 1.5s. cytoscape layout convergence < 2s.
3. **유용성 시그널**: 사내 5명 베타에서 "이 뷰에서 새로 알게 된 인사이트 1개 이상" 응답이 4/5 이상. 클릭 → 드릴다운 → 외부 링크까지 도달률 ≥ 30%.
