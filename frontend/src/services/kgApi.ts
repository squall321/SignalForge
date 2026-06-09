// 지식 그래프 API 클라이언트 — P2-3 T1
//
// 백엔드 라우터: /api/v1/kg/{graph,node/{id}/samples,search}
// (라우터 미구현 시 axios 가 404를 반환 → 페이지에서 빈 상태 표시.)
import api from './api';
import type {
  KGGraphResponse,
  KGNodeSamplesResponse,
  KGSearchResponse,
  KGEdgeType,
} from '../types/kg';

export interface FetchGraphParams {
  start: string;
  end: string;
  top_n: number;
  min_weight: number;
  edge_types: KGEdgeType[];
  products?: string[];
  platforms?: string[];
  regions?: string[];
}

function compactList(v: string[] | undefined): string | undefined {
  return v && v.length > 0 ? v.join(',') : undefined;
}

export async function fetchKGGraph(p: FetchGraphParams): Promise<KGGraphResponse> {
  const params: Record<string, string | number | undefined> = {
    start: p.start || undefined,
    end: p.end || undefined,
    top_n: p.top_n,
    min_weight: p.min_weight,
    edge_types: p.edge_types.join(','),
    products: compactList(p.products),
    platforms: compactList(p.platforms),
    regions: compactList(p.regions),
  };
  const { data } = await api.get<KGGraphResponse>('/kg/graph', { params });
  return data;
}

export async function fetchKGNodeSamples(
  nodeId: string,
  limit: number = 5,
): Promise<KGNodeSamplesResponse> {
  const { data } = await api.get<KGNodeSamplesResponse>(
    `/kg/node/${encodeURIComponent(nodeId)}/samples`,
    { params: { limit } },
  );
  return data;
}

export async function fetchKGSearch(q: string): Promise<KGSearchResponse> {
  if (!q.trim()) return { hits: [] };
  const { data } = await api.get<KGSearchResponse>('/kg/search', { params: { q } });
  return data;
}
