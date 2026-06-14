// 차트 규격 API 래퍼 — backend /api/v1/charts/* 호출.
// 모든 endpoint 가 { chart_type, raw, echarts_option, summary } 표준 응답.
// echarts_option 은 chartTheme.ts 규격 (Okabe-Ito 8색) 이라 ReactECharts 에 그대로 전달.
import api from './api';
import type { EChartsOption } from 'echarts';

export interface ChartResponse {
  chart_type: 'line' | 'bar' | 'graph';
  raw: unknown;
  echarts_option: EChartsOption;
  summary: string;
}

export async function fetchSentimentTimeseries(
  productCodes: string[], days = 90, granularity = 'week',
): Promise<ChartResponse> {
  const params = new URLSearchParams();
  productCodes.forEach((c) => params.append('product_codes', c));
  params.set('days', String(days));
  params.set('granularity', granularity);
  const { data } = await api.get<ChartResponse>(`/charts/sentiment-timeseries?${params}`);
  return data;
}

export async function fetchCountryDistribution(
  productCode?: string, topN = 15,
): Promise<ChartResponse> {
  const { data } = await api.get<ChartResponse>('/charts/country-distribution', {
    params: { product_code: productCode, top_n: topN },
  });
  return data;
}

export async function fetchCategoryDistribution(
  productCode?: string, topN = 15,
): Promise<ChartResponse> {
  const { data } = await api.get<ChartResponse>('/charts/category-distribution', {
    params: { product_code: productCode, top_n: topN },
  });
  return data;
}

export async function fetchCrisisTimeline(caseCode?: string): Promise<ChartResponse> {
  const { data } = await api.get<ChartResponse>('/charts/crisis-timeline', {
    params: { case_code: caseCode },
  });
  return data;
}

export async function fetchKeywordNetwork(
  productCode?: string, days = 30, minCooccur = 3, maxNodes = 40,
): Promise<ChartResponse> {
  const { data } = await api.get<ChartResponse>('/charts/keyword-network', {
    params: { product_code: productCode, days, min_cooccur: minCooccur, max_nodes: maxNodes },
  });
  return data;
}
