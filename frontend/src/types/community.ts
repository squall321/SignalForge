// T3 커뮤니티(플랫폼) 비교 페이지 타입 정의
// platform_health MV(72행) 및 country_daily MV(1016행) 기반의
// 비교/분산/조기신호/클러스터/이상치 분석을 위한 응답 스키마.

export type PlatformStatus = 'active' | 'idle' | 'dead';

// 1) 사이트 상태 — /platforms/health
export interface PlatformHealth {
  platform_id: number;
  code: string;
  region?: string | null;
  base_url?: string | null;
  posts_24h: number;
  posts_7d: number;
  sent_avg_7d: number | null;
  avg_body_len_7d: number | null;
  last_collected?: string | null;
  status: PlatformStatus;
}

export interface PlatformHealthResponse {
  platforms: PlatformHealth[];
  generated_at: string;
}

// 2) 제품 매트릭스 — /platforms/product-matrix
export interface ProductMatrixCell {
  platform_code: string;
  product_code: string;
  count: number;
  sent_avg: number;
}

export interface ProductMatrixResponse {
  cells: ProductMatrixCell[];
  platforms: string[];   // X축 (사이트 코드)
  products: string[];    // Y축 (제품 코드)
}

// 3) 분산 — /platforms/dispersion
// 각 플랫폼별 감성 분포 5수치 (boxplot 입력).
export interface DispersionEntry {
  platform_code: string;
  min: number;
  q1: number;
  median: number;
  q3: number;
  max: number;
  outliers?: number[];   // 시각화용 개별 이상치 점
  n: number;
}

export interface DispersionResponse {
  entries: DispersionEntry[];
}

// 4) Early Signal — /platforms/early-signal
// 특정 신호(=이슈/제품)가 각 플랫폼에 처음 등장한 시각의 lag (선두 대비 hours).
export interface EarlySignalRow {
  platform_code: string;
  signal: string;          // ex. 제품 코드 또는 이슈 라벨
  first_seen: string;      // ISO datetime
  lag_hours: number;       // 0 = 선두
  count_24h: number;       // 첫 등장 후 24h 누적
}

export interface EarlySignalResponse {
  signal: string;
  rows: EarlySignalRow[];
}

// 5) 클러스터 — /platforms/clusters
// 플랫폼을 2D 임베딩(예: PCA/UMAP 좌표) + 클러스터 ID 부여.
export interface ClusterPoint {
  platform_code: string;
  x: number;
  y: number;
  cluster: number;            // 0,1,2,...
  posts_7d: number;
  sent_avg_7d: number;
}

export interface ClusterResponse {
  points: ClusterPoint[];
  k: number;                  // 클러스터 개수
}

// 6) 이상치 — /platforms/anomalies
export type AnomalyKind = 'volume_spike' | 'volume_drop' | 'sent_swing' | 'silence';

export interface AnomalyItem {
  platform_code: string;
  kind: AnomalyKind;
  score: number;             // 0~1, 1에 가까울수록 강함
  detected_at: string;       // ISO datetime
  description: string;
}

export interface AnomalyResponse {
  items: AnomalyItem[];
}
