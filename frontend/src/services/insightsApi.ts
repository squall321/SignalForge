// T4 딥 인사이트 API 클라이언트 (P4 트랙 C).
// 백엔드 /api/v1/insights/* 7 엔드포인트 호출.
import api from './api';
import type {
  CompareLLMRequest,
  CompareLLMResponse,
  EmergingKeywordsResponse,
  HourlyPatternResponse,
  NewTermsResponse,
  PlatformInfluenceResponse,
  ProductLifecycleResponse,
  SentimentSwingResponse,
  WeekdayPatternResponse,
} from '../types/insights';

export async function fetchHourlyPattern(params: {
  product?: string;
  period_days?: number;
}): Promise<HourlyPatternResponse> {
  const { data } = await api.get<HourlyPatternResponse>('/insights/hourly-pattern', {
    params: { period_days: 30, ...params },
  });
  return data;
}

export async function fetchWeekdayPattern(params: {
  product?: string;
  period_days?: number;
}): Promise<WeekdayPatternResponse> {
  const { data } = await api.get<WeekdayPatternResponse>('/insights/weekday-pattern', {
    params: { period_days: 30, ...params },
  });
  return data;
}

export async function fetchEmergingKeywords(params: {
  period_days?: number;
  top_n?: number;
}): Promise<EmergingKeywordsResponse> {
  const { data } = await api.get<EmergingKeywordsResponse>('/insights/emerging-keywords', {
    params: { period_days: 7, top_n: 20, ...params },
  });
  return data;
}

export async function fetchNewTerms(params: {
  period_days?: number;
}): Promise<NewTermsResponse> {
  const { data } = await api.get<NewTermsResponse>('/insights/new-terms', {
    params: { period_days: 30, ...params },
  });
  return data;
}

export async function fetchSentimentSwing(params: {
  period_days?: number;
  min_volume?: number;
}): Promise<SentimentSwingResponse> {
  const { data } = await api.get<SentimentSwingResponse>('/insights/sentiment-swing', {
    params: { period_days: 14, min_volume: 50, ...params },
  });
  return data;
}

export async function fetchProductLifecycle(product: string): Promise<ProductLifecycleResponse> {
  const { data } = await api.get<ProductLifecycleResponse>('/insights/product-lifecycle', {
    params: { product },
  });
  return data;
}

export async function fetchPlatformInfluence(params: {
  period_days?: number;
}): Promise<PlatformInfluenceResponse> {
  const { data } = await api.get<PlatformInfluenceResponse>('/insights/platform-influence', {
    params: { period_days: 30, ...params },
  });
  return data;
}

// 트랙 D — N개 제품의 LLM 비교 분석 narrative
export async function fetchCompareLLM(
  body: CompareLLMRequest,
): Promise<CompareLLMResponse> {
  const { data } = await api.post<CompareLLMResponse>('/insights/compare-llm', body);
  return data;
}
