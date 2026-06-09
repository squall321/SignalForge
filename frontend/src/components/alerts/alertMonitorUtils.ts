// Track A — AlertMonitorPanel 의 순수 변환 유틸.
// React/DOM 의존 없음 (vitest node 환경).
import type {
  AlertHealth,
  AlertMonitorResponse,
  AlertMonitorRule,
} from '../../services/alertsApi';

export const HEALTH_COLOR: Record<AlertHealth, string> = {
  normal: 'green',
  silent: 'default',
  noisy: 'orange',
  violating: 'red',
};

export const HEALTH_LABEL: Record<AlertHealth, string> = {
  normal: '정상',
  silent: '침묵',
  noisy: '과다',
  violating: 'cooldown 위반',
};

/**
 * 7일 발화량을 7개 점으로 단순 보간 + 마지막 점에 24h 가중.
 * 백엔드 trend series 가 없으므로 표시용 sparkline 입력값으로만 사용.
 * fires_7d=0 이면 모두 0 의 평탄선.
 */
export function ruleToSpark(r: AlertMonitorRule): number[] {
  if (r.fires_7d <= 0) return new Array(7).fill(0);
  const base = r.fires_7d / 7;
  return [
    base * 0.6,
    base * 0.8,
    base,
    base,
    base * 1.1,
    base * 1.3,
    r.fires_24h || base,
  ];
}

/**
 * 권고 문장 → AntD Alert type 매핑.
 *   - "cooldown 위반" → error
 *   - "임계 검토"   → warning
 *   - 그 외          → info
 */
export function classifyRecommendation(
  text: string,
): 'error' | 'warning' | 'info' {
  if (text.includes('cooldown 위반')) return 'error';
  if (text.includes('임계 검토')) return 'warning';
  return 'info';
}

/**
 * 룰 health 집계 — 패널 헤더에 "violating 2 / silent 1" 같은 요약 표시할 때 사용.
 * key 가 4종(normal/silent/noisy/violating) 모두 0 이상으로 보장된다.
 */
export function summarizeHealth(
  monitor: AlertMonitorResponse | null,
): Record<AlertHealth, number> {
  const acc: Record<AlertHealth, number> = {
    normal: 0,
    silent: 0,
    noisy: 0,
    violating: 0,
  };
  if (!monitor) return acc;
  for (const r of monitor.rules) {
    acc[r.health] = (acc[r.health] ?? 0) + 1;
  }
  return acc;
}
