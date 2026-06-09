// P5 R6 트랙 A — KPI 카드 순수 계산 헬퍼.
// vitest node 환경(jsdom 미사용) 에서 검증 가능하도록 컴포넌트와 분리.

import type { DashboardKPIs, TrendPoint } from '../../types/dashboard';

export type DeltaDirection = 'up' | 'down' | 'flat';

/** 두 수치의 변화율(%)을 계산. 이전값 0 → null (의미 없음). */
export function pctChange(curr: number, prev: number): number | null {
  if (!Number.isFinite(curr) || !Number.isFinite(prev) || prev === 0) return null;
  return ((curr - prev) / prev) * 100;
}

/** 부호로 방향 분류 — 0 근처(±0.5%)는 flat. */
export function deltaDirection(deltaPct: number | null): DeltaDirection {
  if (deltaPct === null || Math.abs(deltaPct) < 0.5) return 'flat';
  return deltaPct > 0 ? 'up' : 'down';
}

/**
 * "더 많을수록 좋다"는 지표(예: total_voc)인지에 따라 색을 결정.
 * - good=true: up=초록, down=빨강
 * - good=false: up=빨강, down=초록  (예: neg_rate 는 올라가면 나쁨)
 */
export function deltaColor(dir: DeltaDirection, goodWhenUp: boolean): string {
  if (dir === 'flat') return '#8c8c8c';
  const isPositiveSignal = (dir === 'up') === goodWhenUp;
  // 색맹 친화: red/green 대신 amber/teal 도 한 가지 옵션이나
  // 기존 AntD Tag 팔레트와 일관성을 위해 #237804 / #cf1322 사용.
  return isPositiveSignal ? '#237804' : '#cf1322';
}

/** 14일 트렌드를 전반 7일 vs 후반 7일로 나누어 KPI 4종의 Δ% 산출. */
export interface KPIDeltas {
  total_voc: number | null;
  neg_rate: number | null; // pp(percentage-point), 절대 차
  alert_count: number | null;
}

export function computeKPIDeltas(
  trend14d: TrendPoint[],
  kpis: DashboardKPIs,
): KPIDeltas {
  if (!trend14d.length) {
    return { total_voc: null, neg_rate: null, alert_count: null };
  }
  // 마지막 7일 vs 직전 7일 비교.
  const sorted = [...trend14d].sort((a, b) => a.date.localeCompare(b.date));
  const half = Math.floor(sorted.length / 2);
  const prev = sorted.slice(0, half);
  const curr = sorted.slice(half);

  const sumCount = (arr: TrendPoint[]) => arr.reduce((s, p) => s + p.count, 0);
  const avgSent = (arr: TrendPoint[]) =>
    arr.length === 0 ? 0 : arr.reduce((s, p) => s + p.sent_avg, 0) / arr.length;

  const total_voc = pctChange(sumCount(curr), sumCount(prev));

  // neg_rate 는 직접 비교용 14d 가 없으므로,
  // sent_avg 변화량을 proxy(절대 pp) 로 사용 — 음수일수록 부정 증가.
  const sentDelta = avgSent(curr) - avgSent(prev);
  // sent_avg 는 -1~1, neg_rate 는 0~100 → 대략 *50 환산
  const neg_rate = Number.isFinite(sentDelta) ? -sentDelta * 50 : null;

  // alert_count 는 14d trend 에 없음 → null 처리.
  const alert_count = null;

  // 미사용 변수 제거 — kpis 참조는 향후 확장용.
  void kpis;

  return { total_voc, neg_rate, alert_count };
}

/** "오늘의 신호" fallback narrative (LLM 미사용 환경). */
export interface SignalNarrative {
  headline: string;
  bullets: string[];
}

export function buildFallbackSignal(
  overview: {
    kpis: DashboardKPIs;
    trend14d: TrendPoint[];
    top_sites: { code: string; count: number; sent_avg: number }[];
  } | null,
): SignalNarrative {
  if (!overview) {
    return {
      headline: '데이터 로드 대기 중',
      bullets: ['백엔드 응답을 기다리는 중입니다.'],
    };
  }
  const { kpis, trend14d, top_sites } = overview;
  const deltas = computeKPIDeltas(trend14d, kpis);
  const bullets: string[] = [];

  if (deltas.total_voc !== null) {
    const dir = deltaDirection(deltas.total_voc);
    const arrow = dir === 'up' ? '증가' : dir === 'down' ? '감소' : '횡보';
    bullets.push(
      `최근 7일 VOC 유입량은 직전 7일 대비 ${Math.abs(deltas.total_voc).toFixed(1)}% ${arrow}.`,
    );
  }
  if (kpis.top_product) {
    bullets.push(`주목 제품: ${kpis.top_product} — 언급 비중 1위.`);
  }
  if (top_sites.length > 0) {
    const top = top_sites[0];
    const sentTag =
      top.sent_avg < -0.1 ? '부정 우세' : top.sent_avg > 0.1 ? '긍정 우세' : '중립';
    bullets.push(
      `채널 1위: ${top.code} (${top.count.toLocaleString('ko-KR')}건, ${sentTag}).`,
    );
  }
  if (kpis.alert_count > 0) {
    bullets.push(`현재 임계 초과 제품 ${kpis.alert_count}개 — 알림 페이지에서 확인.`);
  }
  if (bullets.length === 0) {
    bullets.push('지난 기간 대비 유의미한 변동이 감지되지 않았습니다.');
  }

  const headline =
    deltas.total_voc !== null && Math.abs(deltas.total_voc) >= 10
      ? `VOC 유입량이 ${deltas.total_voc > 0 ? '급증' : '급감'}했습니다`
      : '오늘의 주요 신호';

  return { headline, bullets };
}
