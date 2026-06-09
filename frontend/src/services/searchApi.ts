// Track E — CommandPalette 통합 검색 API.
// 백엔드 /api/v1/_internal/search 호출. localhost-only 가드는 backend 에서 처리.
import api from './api';

export interface SearchProduct {
  code: string;
  name_ko: string;
  score: number;
}

export interface SearchPlatform {
  code: string;
  name: string;
  region: string | null;
  score: number;
}

export interface SearchCategory {
  code: string;
  name_ko: string;
  score: number;
}

export interface SearchKeyword {
  keyword: string;
  lang: string | null;
  count: number;
  score: number;
}

export interface SearchResponse {
  q: string;
  products: SearchProduct[];
  platforms: SearchPlatform[];
  categories: SearchCategory[];
  keywords: SearchKeyword[];
}

/**
 * 통합 검색. 최소 1자 입력 시만 호출. 빈 쿼리는 빈 응답을 반환한다.
 */
export async function fetchGlobalSearch(
  q: string,
  limit = 15,
): Promise<SearchResponse> {
  const trimmed = q.trim();
  if (!trimmed) {
    return { q: '', products: [], platforms: [], categories: [], keywords: [] };
  }
  const { data } = await api.get<SearchResponse>('/_internal/search', {
    params: { q: trimmed, limit },
  });
  return data;
}
