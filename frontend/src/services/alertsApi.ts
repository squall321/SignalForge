// P4 트랙 A — Alerts REST + WebSocket 래퍼.
import api from './api';

export interface AlertRule {
  id: number;
  name: string;
  metric_path: string;
  op: string;
  threshold: number;
  severity: string;
  cooldown_sec: number;
  description?: string;
  is_active: boolean;
  created_at: string;
}

export interface AlertEvent {
  id: number;
  rule_id: number;
  rule_name: string;
  fired_at: string;
  severity: string;
  value: number;
  threshold: number;
  payload: Record<string, unknown>;
  dispatched_channels: string[];
}

export interface TestResult {
  evaluated: number;
  fired: number;
  metrics: Record<string, number>;
  events: AlertEvent[];
}

export async function fetchRules(): Promise<AlertRule[]> {
  const { data } = await api.get<AlertRule[]>('/alerts/rules');
  return data;
}

export async function fetchRecent(limit = 50): Promise<AlertEvent[]> {
  const { data } = await api.get<AlertEvent[]>('/alerts/recent', { params: { limit } });
  return data;
}

export async function fireTest(): Promise<TestResult> {
  const { data } = await api.post<TestResult>('/alerts/test', {});
  return data;
}

export async function deleteRule(id: number): Promise<void> {
  await api.delete(`/alerts/rules/${id}`);
}

export interface RulePatch {
  is_active?: boolean;
  threshold?: number;
  cooldown_sec?: number;
  severity?: string;
  description?: string;
}

export async function patchRule(id: number, patch: RulePatch): Promise<AlertRule> {
  const { data } = await api.patch<AlertRule>(`/alerts/rules/${id}`, patch);
  return data;
}

export interface RuleCreate {
  name: string;
  metric_path: string;
  op: string;
  threshold: number;
  severity: string;
  cooldown_sec: number;
  description?: string;
  is_active?: boolean;
}

export async function createRule(payload: RuleCreate): Promise<AlertRule> {
  const { data } = await api.post<AlertRule>('/alerts/rules', payload);
  return data;
}

export interface ChannelStatus {
  slack: { enabled: boolean; dry_run: boolean };
  websocket: { connections: number };
}

export async function fetchChannels(): Promise<ChannelStatus> {
  const { data } = await api.get<ChannelStatus>('/alerts/channels');
  return data;
}

// ──────────────────────────────────────────────────────────────
// P4.2 E5 — 룰 프리셋
// ──────────────────────────────────────────────────────────────
export interface AlertPreset {
  key: string;
  name: string;
  metric_path: string;
  op: string;
  threshold: number;
  severity: 'critical' | 'warning' | 'info';
  cooldown_sec: number;
  description?: string;
}

export interface PresetApplyResult {
  requested: number;
  created: number;
  skipped: string[];
  created_rules: AlertRule[];
}

export async function fetchPresets(): Promise<AlertPreset[]> {
  const { data } = await api.get<AlertPreset[]>('/alerts/presets');
  return data;
}

export async function applyPresets(keys: string[]): Promise<PresetApplyResult> {
  const { data } = await api.post<PresetApplyResult>(
    '/alerts/presets/apply',
    { keys },
  );
  return data;
}

// ──────────────────────────────────────────────────────────────
// Track A — 알림 운영 모니터링 (/_internal/alert-monitor)
// ──────────────────────────────────────────────────────────────
export type AlertHealth = 'normal' | 'silent' | 'noisy' | 'violating';

export interface AlertMonitorRule {
  rule_id: number;
  name: string;
  metric_path: string;
  threshold: number;
  cooldown_sec: number;
  severity: string;
  fires_24h: number;
  fires_7d: number;
  avg_value_7d: number | null;
  max_value_7d: number | null;
  last_fired_at: string | null;
  cooldown_violations_24h: number;
  silent_window: boolean;
  health: AlertHealth;
}

export interface AlertMonitorDistEntry {
  p50: number | null;
  p90: number | null;
  p95: number | null;
  p99: number | null;
  n: number;
  current: number | null;
}

export interface AlertMonitorResponse {
  days: number;
  generated_at: string;
  summary: {
    active_rules: number;
    fires_24h: number;
    fires_7d: number;
    cooldown_violations_24h: number;
  };
  rules: AlertMonitorRule[];
  metric_distribution: Record<string, AlertMonitorDistEntry>;
  recommendations: string[];
}

export async function fetchAlertMonitor(
  days = 7,
): Promise<AlertMonitorResponse> {
  const { data } = await api.get<AlertMonitorResponse>(
    '/_internal/alert-monitor',
    { params: { days } },
  );
  return data;
}

export function openAlertSocket(
  onMessage: (msg: { type: string; data: unknown }) => void,
  onError?: (e: Event) => void,
): WebSocket {
  const base = api.defaults.baseURL ?? '';
  const wsUrl = base.replace(/^http/, 'ws') + '/alerts/ws';
  const ws = new WebSocket(wsUrl);
  ws.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch {
      // 무시
    }
  };
  if (onError) ws.onerror = onError;
  return ws;
}
