// P5 R6 트랙 A — Dashboard 백엔드 응답 타입.
// 백엔드 `app/schemas/dashboard.py` 와 1:1 매칭.

export interface DashboardKPIs {
  total_voc: number;
  neg_rate: number; // 0~100
  top_product?: string | null;
  alert_count: number;
}

export interface TrendPoint {
  date: string; // 'YYYY-MM-DD'
  count: number;
  sent_avg: number; // -1~1
}

export interface TopSiteItem {
  code: string;
  count: number;
  sent_avg: number;
}

export interface DashboardOverviewResponse {
  period: string;
  filters: Record<string, unknown>;
  kpis: DashboardKPIs;
  trend14d: TrendPoint[];
  top_sites: TopSiteItem[];
}
