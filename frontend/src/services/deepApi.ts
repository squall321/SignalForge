// P3.6 트랙 C — 심층 분석 8 endpoint 클라이언트.
import api from './api';
import type {
  AnomalyContextResponse,
  AnomalyDrilldownHourResponse,
  AnomalyDrilldownResponse,
  AnomalyWithDriversResponse,
  CategoryMomentumResponse,
  CategoryProductMatrixResponse,
  CountrySentimentGapResponse,
  EngagementSentimentResponse,
  InfluenceRankResponse,
  IssueLifecycleResponse,
  KeywordCooccurrenceResponse,
  KeywordDetailResponse,
  KeywordNetworkResponse,
  LifecycleFunnelResponse,
  NewTermSurvivalResponse,
  ProductFunnelResponse,
  SentimentDriverResponse,
  SiteDiffusionResponse,
} from '../types/deep';

export async function fetchIssueLifecycle(params: {
  category?: string;
  period_days?: number;
  top_n?: number;
} = {}): Promise<IssueLifecycleResponse> {
  const { data } = await api.get('/deep/issue-lifecycle', {
    params: { period_days: 60, top_n: 20, ...params },
  });
  return data;
}

export async function fetchCategoryProductMatrix(params: {
  period_days?: number;
  top_products?: number;
} = {}): Promise<CategoryProductMatrixResponse> {
  const { data } = await api.get('/deep/category-product-matrix', {
    params: { period_days: 30, top_products: 10, ...params },
  });
  return data;
}

export async function fetchSiteDiffusion(params: {
  period_days?: number;
  min_sites?: number;
  top_keywords?: number;
} = {}): Promise<SiteDiffusionResponse> {
  const { data } = await api.get('/deep/site-diffusion', {
    params: { period_days: 45, min_sites: 2, top_keywords: 20, ...params },
  });
  return data;
}

export async function fetchCountrySentimentGap(params: {
  period_days?: number;
  top_products?: number;
  min_n?: number;
} = {}): Promise<CountrySentimentGapResponse> {
  const { data } = await api.get('/deep/country-sentiment-gap', {
    params: { period_days: 30, top_products: 10, min_n: 20, ...params },
  });
  return data;
}

export async function fetchEngagementSentiment(params: {
  period_days?: number;
} = {}): Promise<EngagementSentimentResponse> {
  const { data } = await api.get('/deep/engagement-sentiment', {
    params: { period_days: 30, ...params },
  });
  return data;
}

export async function fetchNewTermSurvival(params: {
  period_days?: number;
  lookback_window?: number;
  min_mentions?: number;
} = {}): Promise<NewTermSurvivalResponse> {
  const { data } = await api.get('/deep/new-term-survival', {
    params: { period_days: 60, lookback_window: 14, min_mentions: 5, ...params },
  });
  return data;
}

export async function fetchKeywordCooccurrence(params: {
  period_days?: number;
  min_edge_weight?: number;
  top_nodes?: number;
} = {}): Promise<KeywordCooccurrenceResponse> {
  const { data } = await api.get('/deep/keyword-cooccurrence', {
    params: { period_days: 30, min_edge_weight: 5, top_nodes: 60, ...params },
  });
  return data;
}

export async function fetchAnomalyContext(params: {
  period_days?: number;
  z_threshold?: number;
} = {}): Promise<AnomalyContextResponse> {
  const { data } = await api.get('/deep/anomaly-context', {
    params: { period_days: 14, z_threshold: 2.5, ...params },
  });
  return data;
}

// ── P3.7 트랙 B 결합 카드 ─────────────────────────────────────────
export async function fetchSentimentDriver(params: {
  period_days?: number;
  top_n?: number;
} = {}): Promise<SentimentDriverResponse> {
  const { data } = await api.get('/deep/sentiment-driver', {
    params: { period_days: 30, top_n: 10, ...params },
  });
  return data;
}

export async function fetchAnomalyWithDrivers(params: {
  period_days?: number;
  z_threshold?: number;
} = {}): Promise<AnomalyWithDriversResponse> {
  const { data } = await api.get('/deep/anomaly-with-drivers', {
    params: { period_days: 14, z_threshold: 2.0, ...params },
  });
  return data;
}

export async function fetchAnomalyDrilldown(params: {
  date: string;
  z_threshold?: number;
  top_k?: number;
}): Promise<AnomalyDrilldownResponse> {
  const { data } = await api.get('/deep/anomaly-drilldown', {
    params: { z_threshold: 2.0, top_k: 10, ...params },
  });
  return data;
}

// E3 — anomaly-drilldown-hour (1h VoC 리스트)
export async function fetchAnomalyDrilldownHour(params: {
  date: string;
  hour: number;
  limit?: number;
  offset?: number;
}): Promise<AnomalyDrilldownHourResponse> {
  const { data } = await api.get('/deep/anomaly-drilldown-hour', {
    params: { limit: 20, offset: 0, ...params },
  });
  return data;
}

// ── 트랙 D: 추가 deep cut 5 endpoint ────────────────────────────
export async function fetchCategoryMomentum(params: {
  period_days?: number;
  bucket?: 'week' | 'day';
} = {}): Promise<CategoryMomentumResponse> {
  const { data } = await api.get('/deep/category-momentum', {
    params: { period_days: 60, bucket: 'week', ...params },
  });
  return data;
}

export async function fetchKeywordNetwork(params: {
  period_days?: number;
  min_cooccur?: number;
  max_nodes?: number;
} = {}): Promise<KeywordNetworkResponse> {
  const { data } = await api.get('/deep/keyword-network', {
    params: { period_days: 30, min_cooccur: 10, max_nodes: 80, ...params },
  });
  return data;
}

export async function fetchLifecycleFunnel(params: {
  period_days?: number;
} = {}): Promise<LifecycleFunnelResponse> {
  const { data } = await api.get('/deep/lifecycle-funnel', {
    params: { period_days: 90, ...params },
  });
  return data;
}

export async function fetchInfluenceRank(params: {
  period_days?: number;
  top_n?: number;
} = {}): Promise<InfluenceRankResponse> {
  const { data } = await api.get('/deep/influence-rank', {
    params: { period_days: 30, top_n: 30, ...params },
  });
  return data;
}

export async function fetchProductFunnel(params: {
  product: string;
  period_days?: number;
}): Promise<ProductFunnelResponse> {
  const { data } = await api.get('/deep/product-funnel', {
    params: { period_days: 180, ...params },
  });
  return data;
}

// ── UX R2 트랙 A: KeywordNetwork node 클릭 → 키워드 상세 ──────
export async function fetchKeywordDetail(params: {
  keyword: string;
  lang?: string | null;
  period_days?: number;
  limit?: number;
}): Promise<KeywordDetailResponse> {
  const { lang, ...rest } = params;
  const { data } = await api.get('/deep/keyword-detail', {
    params: { period_days: 7, limit: 5, ...(lang ? { lang } : {}), ...rest },
  });
  return data;
}
