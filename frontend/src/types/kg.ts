// 지식 그래프(Knowledge Graph) 타입 정의 — P2-3 T1
//
// 백엔드 /api/v1/kg/* 응답 스키마와 1:1 대응.
// - 노드: id = "type:code" (예: "product:GS25U", "category:battery", "platform:reddit", "country:KR")
// - 엣지: (source, target, weight, sent_avg, edge_type)
// kg_edges_daily MV 의 product↔category / product↔platform / product↔country 3가지 edge_type 지원.

export type KGNodeType = 'product' | 'category' | 'platform' | 'country';

export type KGEdgeType = 'product_category' | 'product_platform' | 'product_country';

export interface KGNode {
  id: string;          // 예: "product:GS25U"
  label: string;       // 표시명
  type: KGNodeType;
  count: number;       // 누적 weight 또는 등장 횟수
  sent_avg: number | null; // -1.0 ~ +1.0 평균 감성
}

export interface KGEdge {
  source: string;
  target: string;
  weight: number;
  sent_avg: number | null;
  edge_type: KGEdgeType;
}

export interface KGGraphResponse {
  nodes: KGNode[];
  edges: KGEdge[];
}

export interface KGSample {
  voc_id: number;
  title: string | null;
  excerpt: string;
  platform_code: string | null;
  country_code: string | null;
  sentiment_score: number | null;
  url: string | null;
  collected_at: string; // ISO
}

export interface KGNodeSamplesResponse {
  node_id: string;
  samples: KGSample[];
}

export interface KGSearchHit {
  id: string;
  label: string;
  type: KGNodeType;
  count: number;
}

export interface KGSearchResponse {
  hits: KGSearchHit[];
}

// 컨트롤 패널 상태
export interface KGControls {
  topN: number;          // 노드 상위 개수 (40 ~ 200)
  minWeight: number;     // 엣지 최소 weight (1 ~ 50)
  edgeTypes: KGEdgeType[];
}

export const DEFAULT_KG_CONTROLS: KGControls = {
  topN: 80,
  minWeight: 3,
  edgeTypes: ['product_category', 'product_platform', 'product_country'],
};

// 노드 type → 색상 매핑 (요구사항)
export const NODE_TYPE_COLOR: Record<KGNodeType, string> = {
  product: '#1677ff',   // blue
  category: '#52c41a',  // green
  platform: '#fa8c16',  // orange
  country: '#722ed1',   // purple
};
