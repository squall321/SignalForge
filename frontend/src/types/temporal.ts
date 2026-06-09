// T2 시계열 인사이트 타입 정의

export type CompareMode = 'products' | 'periods' | 'categories';

export interface TemporalPoint {
  date: string;        // YYYY-MM-DD
  count: number;       // VOC 건수
  sent_avg: number;    // 평균 감성 점수 (-1 ~ 1)
}

export interface TemporalSeries {
  key: string;         // product_code | period_label | category_code
  label: string;       // 사용자 노출용
  data: TemporalPoint[];
}

export interface TimelineEvent {
  date: string;        // YYYY-MM-DD
  title: string;       // 이벤트 타이틀
  category?: string;   // launch | update | issue | etc.
  product_code?: string;
}

export interface Changepoint {
  date: string;
  series_key: string;
  delta: number;       // 변화 폭
  reason?: string;     // 추정 사유
}

export interface TemporalSeriesResponse {
  mode: CompareMode;
  series: TemporalSeries[];
  events: TimelineEvent[];
  changepoints: Changepoint[];
}

// LLM Narrative 요청/응답
export interface LLMNarrativeCitation {
  source_url: string;
  title?: string;
  snippet?: string;
}

export interface LLMNarrativeRequest {
  mode: CompareMode;
  series_keys: string[];
  date_start: string;
  date_end: string;
  // 차트에서 본 데이터를 LLM이 분석할 수 있도록 컨텍스트로 첨부
  context?: {
    series: TemporalSeries[];
    events: TimelineEvent[];
    changepoints: Changepoint[];
  };
}

export interface LLMNarrativeResponse {
  narrative: string;            // Markdown 본문 (한국어)
  citations: LLMNarrativeCitation[];
  model?: string;               // 응답 모델명 (qwen2.5:7b 등)
  generated_at?: string;        // ISO timestamp
}
