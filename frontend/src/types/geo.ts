// T4 국가 분석 페이지 타입 정의

export interface CountryMetric {
  country_code: string;      // ISO 3166-1 alpha-3 (예: USA, KOR, JPN)
  country_name?: string;
  count: number;             // 기간 내 VoC 건수
  sent_avg: number;          // 평균 감성 (-1 ~ 1)
  sent_z?: number;           // 글로벌 평균 대비 z-score
}

export interface CountryHeatmapResponse {
  countries: CountryMetric[];
  start: string;
  end: string;
}

export interface CountryTopSite {
  site_code: string;
  site_name?: string;
  count: number;
  sent_avg: number;
}

export interface CountryTopProduct {
  product_code: string;
  product_name?: string;
  count: number;
  sent_avg: number;
}

export interface CountryTopCategory {
  category: string;
  count: number;
  sent_avg: number;
}

export interface CountryDrilldownResponse {
  country_code: string;
  country_name?: string;
  total_count: number;
  sent_avg: number;
  top_sites: CountryTopSite[];
  top_products: CountryTopProduct[];
  top_categories: CountryTopCategory[];
}

export interface DiffusionFrame {
  date: string;                                     // YYYY-MM-DD
  values: Record<string, number>;                   // country_code → metric (count or sent_z)
}

export interface DiffusionResponse {
  metric: 'count' | 'sent_z';
  frames: DiffusionFrame[];
}

export interface ProductCompareItem {
  country_code: string;
  country_name?: string;
  product_code: string;
  sent_avg: number;
  sent_ci_low?: number;   // 95% CI lower
  sent_ci_high?: number;  // 95% CI upper
  count: number;
}

export interface ProductCompareResponse {
  product_code: string;
  items: ProductCompareItem[];
}

// 색상 모드 선택
export type ChoroplethMode = 'count' | 'sent_z';
