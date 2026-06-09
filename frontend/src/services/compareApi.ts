// P5 R7 (키-없이 라운드) — Compare 페이지 전용 API 래퍼.
// 4 제품 동시 비교를 위해 기존 endpoint 들을 1 회 호출로 묶어 반환한다.
//
// 사용 endpoint
//  - /dashboard/overview?product=X&period=7d        → 24h count, active sites
//  - /analytics/cohort-compare?dimension=sentiment   → 7d sentiment_avg, neg_rate
//  - /analytics/cohort-compare?dimension=category    → 카테고리 stacked bar
//  - /analytics/sentiment-trend?product=X            → 시계열 line
//  - /analytics/top-issues?product=X                 → 부정 키워드 표
//
// 모든 호출은 Promise.all 로 병렬 실행. 일부 실패해도 다른 카드는 살리도록 settle 처리.
import api from './api';
import type { DashboardOverviewResponse } from '../types/dashboard';

// ── 응답 sub-shape (백엔드 schemas/analytics.py 와 1:1 매칭) ──
export interface SentimentTrendPoint {
  date: string;
  positive: number;
  negative: number;
  neutral: number;
  avg_score: number;
}
export interface SentimentTrendResponse {
  product_code: string;
  granularity: string;
  data: SentimentTrendPoint[];
}

export interface IssueRanking {
  rank: number;
  category: string;
  name_ko?: string | null;
  count: number;
  negative_rate: number;
  sample_texts: string[];
}
export interface TopIssuesResponse {
  product_code: string;
  period_days: number;
  issues: IssueRanking[];
}

export interface CohortSentimentMetric {
  product_code: string;
  product_name: string;
  total: number;
  positive: number;
  negative: number;
  neutral: number;
  positive_rate: number;
  negative_rate: number;
  avg_score: number;
}
export interface CohortCategoryItem {
  category: string;
  count: number;
}
export interface CohortCategoryMetric {
  product_code: string;
  product_name: string;
  total: number;
  categories: CohortCategoryItem[];
}
export interface CohortCompareResponse {
  dimension: string;
  period_days: number;
  products: string[];
  sentiment?: CohortSentimentMetric[] | null;
  category?: CohortCategoryMetric[] | null;
}

// ── 묶음 응답 — Compare 페이지 single source of truth ──
export interface CompareData {
  productCodes: string[];
  periodDays: number;
  // 제품별 7d cohort sentiment (KPI 행 2·3)
  sentiment7d: CohortSentimentMetric[];
  // 카테고리 stacked bar (period 전 범위)
  categories: CohortCategoryMetric[];
  // 제품별 overview (24h count = 마지막 trend14d point, active sites = top_sites.length)
  overviews: Record<string, DashboardOverviewResponse | null>;
  // 제품별 시계열
  trends: Record<string, SentimentTrendResponse | null>;
  // 제품별 부정 키워드 top 5
  topIssues: Record<string, TopIssuesResponse | null>;
}

// Promise.allSettled wrapper — 실패 시 null 로 강등.
async function safe<T>(p: Promise<T>): Promise<T | null> {
  try {
    return await p;
  } catch {
    return null;
  }
}

export async function fetchCompareData(
  productCodes: string[],
  periodDays: number,
): Promise<CompareData> {
  const cleaned = productCodes.filter(Boolean).slice(0, 4);
  if (cleaned.length === 0) {
    return {
      productCodes: [],
      periodDays,
      sentiment7d: [],
      categories: [],
      overviews: {},
      trends: {},
      topIssues: {},
    };
  }

  const csv = cleaned.join(',');

  // 1) cohort 2종 (sentiment 7d 고정 / category period 그대로)
  const cohortSentimentP = safe(
    api
      .get<CohortCompareResponse>('/analytics/cohort-compare', {
        params: { products: csv, dimension: 'sentiment', period_days: 7 },
      })
      .then((r) => r.data),
  );
  const cohortCategoryP = safe(
    api
      .get<CohortCompareResponse>('/analytics/cohort-compare', {
        params: { products: csv, dimension: 'category', period_days: periodDays },
      })
      .then((r) => r.data),
  );

  // 2) 제품별 3종 병렬
  const perProduct = cleaned.map(async (code) => {
    const [overview, trend, top] = await Promise.all([
      safe(
        api
          .get<DashboardOverviewResponse>('/dashboard/overview', {
            params: { product: code, period: '7d' },
          })
          .then((r) => r.data),
      ),
      safe(
        api
          .get<SentimentTrendResponse>('/analytics/sentiment-trend', {
            params: { product: code, period_days: periodDays, granularity: 'day' },
          })
          .then((r) => r.data),
      ),
      safe(
        api
          .get<TopIssuesResponse>('/analytics/top-issues', {
            params: { product: code, period_days: periodDays, top_n: 5 },
          })
          .then((r) => r.data),
      ),
    ]);
    return { code, overview, trend, top };
  });

  const [cohortSent, cohortCat, perResults] = await Promise.all([
    cohortSentimentP,
    cohortCategoryP,
    Promise.all(perProduct),
  ]);

  const overviews: Record<string, DashboardOverviewResponse | null> = {};
  const trends: Record<string, SentimentTrendResponse | null> = {};
  const topIssues: Record<string, TopIssuesResponse | null> = {};
  for (const r of perResults) {
    overviews[r.code] = r.overview;
    trends[r.code] = r.trend;
    topIssues[r.code] = r.top;
  }

  return {
    productCodes: cleaned,
    periodDays,
    sentiment7d: cohortSent?.sentiment ?? [],
    categories: cohortCat?.category ?? [],
    overviews,
    trends,
    topIssues,
  };
}
