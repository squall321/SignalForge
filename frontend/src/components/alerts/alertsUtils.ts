// P4 트랙 D — Alerts UI 도우미.
// React/DOM 의존 없음 (vitest 환경이 node 라 jsdom 없음).
import type { AlertEvent } from '../../services/alertsApi';

export type Severity = 'critical' | 'warning' | 'info';

export interface RuleFormValues {
  name: string;
  metric_path: string;
  op: string;
  threshold: number;
  severity: Severity;
  cooldown_sec: number;
  description?: string;
}

/** 폼 값 → 백엔드 POST 페이로드 (is_active=true 강제, undefined 제거). */
export function buildRulePayload(v: RuleFormValues) {
  return {
    name: v.name.trim(),
    metric_path: v.metric_path.trim(),
    op: v.op,
    threshold: Number(v.threshold),
    severity: v.severity,
    cooldown_sec: Number(v.cooldown_sec),
    description: v.description?.trim() || undefined,
    is_active: true,
  };
}

/** 최소 검증 — Form rules 로 잡지 못한 경우의 안전망. */
export function validateRuleForm(v: Partial<RuleFormValues>): string | null {
  if (!v.name || v.name.trim().length === 0) return 'name 필수';
  if (!v.metric_path || v.metric_path.trim().length === 0) return 'metric_path 필수';
  if (!v.op || !['>', '<', '>=', '<=', '=='].includes(v.op)) return 'op 형식 오류';
  if (v.threshold === undefined || Number.isNaN(Number(v.threshold))) return 'threshold 숫자 필요';
  if (!v.severity || !['critical', 'warning', 'info'].includes(v.severity)) return 'severity 필수';
  if (!v.cooldown_sec || Number(v.cooldown_sec) < 10) return 'cooldown_sec 최소 10';
  return null;
}

export interface TimelineBucket {
  hour: string; // 'YYYY-MM-DD HH:00'
  critical: number;
  warning: number;
  info: number;
  total: number;
}

/**
 * AlertEvent[] → 지난 N 일 시간대(UTC 기준 YYYY-MM-DD HH:00) 별 severity count.
 * fired_at 이 ISO 문자열. 비어있으면 skip.
 *
 * since: epoch ms. 이전 이벤트는 제외.
 */
export function bucketEventsByHour(
  events: AlertEvent[],
  since: number,
): TimelineBucket[] {
  const buckets = new Map<string, TimelineBucket>();
  for (const ev of events) {
    if (!ev.fired_at) continue;
    const t = Date.parse(ev.fired_at);
    if (Number.isNaN(t) || t < since) continue;
    const d = new Date(t);
    const yyyy = d.getUTCFullYear();
    const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(d.getUTCDate()).padStart(2, '0');
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const key = `${yyyy}-${mm}-${dd} ${hh}:00`;
    const b =
      buckets.get(key) ?? { hour: key, critical: 0, warning: 0, info: 0, total: 0 };
    const sev = (ev.severity as Severity) ?? 'info';
    if (sev === 'critical') b.critical += 1;
    else if (sev === 'warning') b.warning += 1;
    else b.info += 1;
    b.total += 1;
    buckets.set(key, b);
  }
  return Array.from(buckets.values()).sort((a, b) =>
    a.hour < b.hour ? -1 : a.hour > b.hour ? 1 : 0,
  );
}

/** 프리셋 picker 의 multi-select toggle — Set 불변 갱신. */
export function togglePresetKey(prev: Set<string>, key: string): Set<string> {
  const next = new Set(prev);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

/** 알려진 metric_path 목록 — Select 옵션. */
export const KNOWN_METRIC_PATHS: { value: string; label: string }[] = [
  {
    value: 'community.extreme_negative_count',
    label: 'community.extreme_negative_count (감성≤-0.3 플랫폼 수)',
  },
  {
    value: 'community.negative_rate_max',
    label: 'community.negative_rate_max (부정 비율 추정 최대)',
  },
  {
    value: 'insights.new_term_spike_count',
    label: 'insights.new_term_spike_count (신조어 ≥20건 개수)',
  },
];
