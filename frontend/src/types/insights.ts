// T4 딥 인사이트 타입 정의 (P4 트랙 C)

// 1) hourly-pattern
export interface HourlyPoint {
  hour: number;       // 0..23
  count: number;
  sent_avg: number;
}
export interface HourlyPatternResponse {
  points: HourlyPoint[];
  meta: Record<string, unknown>;
}

// 2) weekday-pattern
export interface WeekdayPoint {
  weekday: number;    // 0=Mon..6=Sun
  label: string;      // 'Mon'..'Sun'
  count: number;
  sent_avg: number;
  neg_rate: number;
}
export interface WeekdayPatternResponse {
  points: WeekdayPoint[];
  meta: Record<string, unknown>;
}

// 3) emerging-keywords
export interface KeywordTrend {
  keyword: string;
  lang?: string | null;
  prev_week_count: number;
  this_week_count: number;
  growth_pct: number;
}
export interface EmergingKeywordsResponse {
  emerging: KeywordTrend[];
  declining: KeywordTrend[];
  meta: Record<string, unknown>;
}

// 4) new-terms
export interface NewTermEntry {
  keyword: string;
  lang?: string | null;
  first_seen: string;
  count_recent: number;
}
export interface NewTermsResponse {
  items: NewTermEntry[];
  meta: Record<string, unknown>;
}

// 5) sentiment-swing
export interface SentimentSwingEntry {
  product: string;
  before_sent: number;
  after_sent: number;
  delta_pp: number;
  n_before: number;
  n_after: number;
}
export interface SentimentSwingResponse {
  items: SentimentSwingEntry[];
  meta: Record<string, unknown>;
}

// 6) product-lifecycle
export interface LifecyclePoint {
  d_offset: number;
  window_from: string;
  window_to: string;
  count: number;
  sent_avg: number;
  top_categories: string[];
}
export interface ProductLifecycleResponse {
  product: string;
  release_date?: string | null;
  points: LifecyclePoint[];
  meta: Record<string, unknown>;
}

// 7) platform-influence
export interface InfluenceDrivers {
  engagement: number;
  neg_rate: number;
  lag_days: number;
}
export interface PlatformInfluenceEntry {
  platform: string;
  region?: string | null;
  score: number;
  n: number;
  drivers: InfluenceDrivers;
}
export interface PlatformInfluenceResponse {
  items: PlatformInfluenceEntry[];
  meta: Record<string, unknown>;
}

// 8) compare-llm (트랙 D)
export interface CompareLLMRequest {
  products: string[];   // 2~4
  period_days: number;
}
export interface CompareLLMResponse {
  narrative: string | null;
  tier_label: string;
  grounding_score: number;
  generated_at: string;
  products: string[];
  period_days: number;
}
