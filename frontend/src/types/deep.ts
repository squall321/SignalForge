// P3.6 트랙 C — 심층 분석 8 endpoint 응답 타입 (backend/app/schemas/deep.py 와 1:1).

// 1) issue-lifecycle
export interface LifecycleItem {
  category: string | null;
  keyword: string;
  first_seen: string;
  peak_day: string;
  last_seen: string;
  days_to_peak: number;
  lifespan: number;
  intensity: number;
}
export interface LifecycleCategoryAvg {
  category: string;
  avg_lifespan: number;
  avg_days_to_peak: number;
  n_issues: number;
}
export interface IssueLifecycleResponse {
  items: LifecycleItem[];
  category_avg: LifecycleCategoryAvg[];
  meta: Record<string, unknown>;
}

// 2) category-product-matrix
export interface MatrixCell {
  product: string;
  category: string;
  score: number;
  n: number;
  zscore: number | null;
  flag: 'outlier_neg' | 'outlier_pos' | 'normal';
}
export interface CategoryProductMatrixResponse {
  products: string[];
  categories: string[];
  cells: MatrixCell[];
  meta: Record<string, unknown>;
}

// 3) site-diffusion
export interface DiffusionHop {
  site: string;
  first_seen: string;
  hop: number;
  lag_days: number | null;
}
export interface DiffusionKeyword {
  keyword: string;
  path: DiffusionHop[];
  total_span_days: number;
  origin_site: string;
  terminal_site: string;
}
export interface DiffusionEdge {
  from_site: string;
  to_site: string;
  count: number;
  avg_lag: number;
}
export interface SiteDiffusionResponse {
  keywords: DiffusionKeyword[];
  edges: DiffusionEdge[];
  meta: Record<string, unknown>;
}

// 4) country-sentiment-gap
export interface CountrySentimentItem {
  product: string;
  country: string;
  score: number;
  n: number;
  gap_vs_global: number;
}
export interface TopGapEntry {
  product: string;
  country_high: string;
  country_low: string;
  gap: number;
}
export interface CountrySentimentGapResponse {
  items: CountrySentimentItem[];
  top_gaps: TopGapEntry[];
  meta: Record<string, unknown>;
}

// 5) engagement-sentiment
export interface EngagementBucket {
  bucket: number;
  eng_range: string;
  score: number;
  neg_ratio: number;
  n: number;
}
export interface EngagementByCategory {
  category: string;
  corr_eng_neg: number;
  top_bucket: number;
}
export interface EngagementSentimentResponse {
  buckets: EngagementBucket[];
  by_category: EngagementByCategory[];
  meta: Record<string, unknown>;
}

// 6) new-term-survival
export interface SurvivalItem {
  keyword: string;
  first_day: string;
  last_day: string;
  survival_days: number;
  active_days: number;
  total: number;
  cls: 'sustained' | 'mid' | 'flash';
}
export interface SurvivalSummary {
  sustained: number;
  mid: number;
  flash: number;
  avg_survival: number;
}
export interface NewTermSurvivalResponse {
  items: SurvivalItem[];
  summary: SurvivalSummary;
  meta: Record<string, unknown>;
}

// 7) keyword-cooccurrence
export interface CooccurNode {
  id: string;
  degree: number;
  sentiment_bias: number;
}
export interface CooccurEdge {
  from: string;
  to: string;
  weight: number;
  lift: number;
}
export interface CooccurPair {
  k1: string;
  k2: string;
  weight: number;
  lift: number;
  sentiment_skew: number;
}
export interface KeywordCooccurrenceResponse {
  nodes: CooccurNode[];
  edges: CooccurEdge[];
  top_pairs: CooccurPair[];
  meta: Record<string, unknown>;
}

// 8) anomaly-context
export interface KeywordDelta {
  keyword: string;
  before: number;
  after: number;
  delta: number;
}
export interface MatchedEvent {
  title: string;
  event_date: string;
  lag_days: number;
}
export interface SpikeEntry {
  date: string;
  category: string;
  count: number;
  z: number;
  top_keywords_delta: KeywordDelta[];
  matched_events: MatchedEvent[];
  inferred_cause: string | null;
}
export interface AnomalyContextResponse {
  spikes: SpikeEntry[];
  meta: Record<string, unknown>;
}

// 9) sentiment-driver (P3.7 트랙 B 결합 카드)
export interface SentimentDriverItem {
  keyword: string;
  lang: string | null;
  before_neg_rate: number;
  after_neg_rate: number;
  delta_pp: number;
  n_before: number;
  n_after: number;
  related_categories: string[];
}
export interface SentimentDriverResponse {
  items: SentimentDriverItem[];
  meta: Record<string, unknown>;
}

// 10) anomaly-with-drivers (P3.7 트랙 B 결합 카드)
export interface TopDriver {
  keyword: string;
  delta_pct: number;
  sentiment: number;
}
export interface AnomalyWithDriversEntry {
  date: string;
  metric: string;
  category: string;
  z: number;
  baseline: number;
  value: number;
  top_drivers: TopDriver[];
}
export interface AnomalyWithDriversResponse {
  anomalies: AnomalyWithDriversEntry[];
  meta: Record<string, unknown>;
}

// 11) anomaly-drilldown (P4.1 트랙 B — Drawer cross drill-down)
export interface AnomalySummary {
  z: number;
  value: number;
  baseline: number;
}
export interface DrilldownHourBucket {
  hour: number;
  count: number;
  sent_avg: number;
  neg_rate: number;
}
export interface DrilldownProduct {
  code: string;
  name_ko: string | null;
  count: number;
  neg_rate: number;
}
export interface DrilldownKeyword {
  keyword: string;
  lang: string | null;
  count: number;
  delta_pct: number;
  related_products: string[];
}
export interface DrilldownPlatform {
  code: string;
  name: string | null;
  count: number;
}
export interface AnomalyDrilldownResponse {
  date: string;
  anomaly_summary: AnomalySummary;
  hourly: DrilldownHourBucket[];
  products: DrilldownProduct[];
  keywords: DrilldownKeyword[];
  platforms: DrilldownPlatform[];
  meta: Record<string, unknown>;
}

// 11.5) anomaly-drilldown-hour (E3 — 1h VoC 리스트)
export interface DrilldownHourProductRef {
  code: string;
  name_ko: string | null;
}
export interface DrilldownHourPlatformRef {
  code: string;
  name: string | null;
}
export interface DrilldownHourVocItem {
  id: number;
  product: DrilldownHourProductRef | null;
  platform: DrilldownHourPlatformRef | null;
  content_preview: string;
  sentiment_label: 'positive' | 'negative' | 'neutral' | null;
  sentiment_score: number | null;
  engagement_score: number | null;
  url: string | null;
  published_at: string | null;
}
export interface AnomalyDrilldownHourResponse {
  date: string;
  hour: number;
  total: number;
  items: DrilldownHourVocItem[];
  meta: Record<string, unknown>;
}

// ── D-track v2 ─────────────────────────────────────────────────

// D1) category-momentum
export interface MomentumWeekPoint {
  week: string;
  share_pct: number;
  n: number;
}
export interface CategoryMomentumItem {
  code: string;
  name_ko: string | null;
  series: MomentumWeekPoint[];
  momentum_slope: number;
}
export interface CategoryMomentumResponse {
  categories: CategoryMomentumItem[];
  meta: Record<string, unknown>;
}

// D2) keyword-network
export interface NetworkNode {
  id: string;
  keyword: string;
  lang: string | null;
  freq: number;
  community_id: number;
}
export interface NetworkEdge {
  source: string;
  target: string;
  weight: number;
}
export interface KeywordNetworkResponse {
  nodes: NetworkNode[];
  edges: NetworkEdge[];
  meta: Record<string, unknown>;
}

// D3) lifecycle-funnel
export interface LifecycleFunnelExample {
  keyword: string;
  days_alive: number;
  peak_count: number;
}
export interface LifecycleFunnelStage {
  stage: string;
  n_keywords: number;
  examples: LifecycleFunnelExample[];
}
export interface LifecycleFunnelResponse {
  stages: LifecycleFunnelStage[];
  meta: Record<string, unknown>;
}

// D4) influence-rank
export interface InfluenceDrivers {
  engagement: number;
  neg_rate: number;
  lead_days: number;
  reach: number;
}
export interface InfluenceRankItem {
  platform: string;
  code: string;
  region: string | null;
  score: number;
  drivers: InfluenceDrivers;
}
export interface InfluenceRankResponse {
  items: InfluenceRankItem[];
  meta: Record<string, unknown>;
}

// D5) product-funnel
export interface ProductFunnelStage {
  stage: string;
  period: string;
  count: number;
  sent_avg: number;
  top_keywords: string[];
}
export interface ProductFunnelResponse {
  product: string;
  stages: ProductFunnelStage[];
  meta: Record<string, unknown>;
}

// ── UX R2 트랙 A) keyword-detail (Network 노드 클릭 → Drawer) ─────
export interface KeywordDetailProductStat {
  code: string;
  name_ko: string | null;
  count: number;
}
export interface KeywordDetailPlatformStat {
  code: string;
  name: string | null;
  count: number;
}
export interface KeywordDetailStats {
  total_count: number;
  sentiment_avg: number;
  top_products: KeywordDetailProductStat[];
  top_platforms: KeywordDetailPlatformStat[];
}
export interface KeywordDetailSample {
  id: number;
  content_preview: string;
  sentiment_label: 'positive' | 'negative' | 'neutral' | null;
  product: string | null;
  platform: string | null;
  url: string | null;
  published_at: string | null;
}
export interface KeywordDetailRelated {
  keyword: string;
  lang: string | null;
  cooccur_count: number;
}
export interface KeywordDetailCategory {
  category: string;
  count: number;
}
export interface KeywordDetailResponse {
  keyword: string;
  lang: string | null;
  period_days: number;
  stats: KeywordDetailStats;
  samples: KeywordDetailSample[];
  related_keywords: KeywordDetailRelated[];
  categories: KeywordDetailCategory[];
  meta: Record<string, unknown>;
}

// ── R9 트랙 A: galaxy-history ────────────────────────────────────
export interface GalaxyTimelineModel {
  code: string;
  name: string;
  series: string;
  released_at: string | null;
  voc_7d_count: number;
  sent_avg: number;
  neg_rate: number;
  peak_count: number;
  total_count: number;
}
export interface GalaxyTimelineResponse {
  series: string;
  models: GalaxyTimelineModel[];
  meta: Record<string, unknown>;
}

export interface CrisisCaseTimelinePoint {
  day: string;
  count: number;
}
export interface CrisisCaseKeyword {
  keyword: string;
  count: number;
}
export interface CrisisCaseSite {
  site: string;
  count: number;
}
export interface CrisisCase {
  code: string;
  title: string;
  description: string;
  period_start: string;
  period_end: string;
  total_voc: number;
  neg_rate: number;
  timeline: CrisisCaseTimelinePoint[];
  top_keywords: CrisisCaseKeyword[];
  top_sites: CrisisCaseSite[];
}
export interface CrisisCasesResponse {
  cases: CrisisCase[];
  meta: Record<string, unknown>;
}

export interface SeriesComparisonGenPoint {
  gen: number;
  code: string;
  name: string;
  released_at: string | null;
  count: number;
  sent_avg: number;
  neg_rate: number;
}
export interface SeriesComparisonSeries {
  series: string;
  label: string;
  points: SeriesComparisonGenPoint[];
}
export interface SeriesComparisonResponse {
  series_list: SeriesComparisonSeries[];
  meta: Record<string, unknown>;
}
