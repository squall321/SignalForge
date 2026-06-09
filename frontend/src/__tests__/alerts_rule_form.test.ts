// P4 트랙 D — RuleFormModal 유틸 + timeline 버킷 단위 테스트.
// vitest 환경이 node 라 DOM 미사용. RuleFormModal 의 onOk 가 의존하는
// buildRulePayload / validateRuleForm / bucketEventsByHour 만 검증.
import { describe, it, expect } from 'vitest';
import {
  buildRulePayload,
  validateRuleForm,
  bucketEventsByHour,
  KNOWN_METRIC_PATHS,
  type RuleFormValues,
} from '../components/alerts/alertsUtils';
import type { AlertEvent } from '../services/alertsApi';

const okForm: RuleFormValues = {
  name: '  my_rule  ',
  metric_path: 'community.extreme_negative_count',
  op: '>=',
  threshold: 3,
  severity: 'critical',
  cooldown_sec: 900,
  description: '  설명  ',
};

describe('buildRulePayload', () => {
  it('trim + is_active 강제 + 숫자 변환', () => {
    const p = buildRulePayload(okForm);
    expect(p.name).toBe('my_rule');
    expect(p.metric_path).toBe('community.extreme_negative_count');
    expect(p.op).toBe('>=');
    expect(p.threshold).toBe(3);
    expect(p.severity).toBe('critical');
    expect(p.cooldown_sec).toBe(900);
    expect(p.description).toBe('설명');
    expect(p.is_active).toBe(true);
  });
  it('빈 description → undefined', () => {
    const p = buildRulePayload({ ...okForm, description: '   ' });
    expect(p.description).toBeUndefined();
  });
});

describe('validateRuleForm', () => {
  it('정상 입력 → null', () => {
    expect(validateRuleForm(okForm)).toBeNull();
  });
  it('name 누락', () => {
    expect(validateRuleForm({ ...okForm, name: '   ' })).toMatch(/name/);
  });
  it('op 형식 오류', () => {
    expect(validateRuleForm({ ...okForm, op: '!=' })).toMatch(/op/);
  });
  it('cooldown 너무 짧음', () => {
    expect(validateRuleForm({ ...okForm, cooldown_sec: 5 })).toMatch(/cooldown/);
  });
  it('severity 누락', () => {
    expect(
      validateRuleForm({ ...okForm, severity: 'urgent' as never }),
    ).toMatch(/severity/);
  });
});

describe('KNOWN_METRIC_PATHS', () => {
  it('seed 룰 3종 모두 포함', () => {
    const vals = KNOWN_METRIC_PATHS.map((m) => m.value);
    expect(vals).toContain('community.extreme_negative_count');
    expect(vals).toContain('community.negative_rate_max');
    expect(vals).toContain('insights.new_term_spike_count');
  });
});

const ev = (id: number, sev: string, iso: string): AlertEvent => ({
  id,
  rule_id: 1,
  rule_name: 'r',
  fired_at: iso,
  severity: sev,
  value: 1,
  threshold: 0,
  payload: {},
  dispatched_channels: [],
});

describe('bucketEventsByHour', () => {
  it('동일 시간대 합산 + severity 별 카운트', () => {
    const events = [
      ev(1, 'critical', '2026-06-02T11:05:00Z'),
      ev(2, 'warning', '2026-06-02T11:30:00Z'),
      ev(3, 'info', '2026-06-02T12:00:00Z'),
    ];
    const since = Date.parse('2026-06-01T00:00:00Z');
    const buckets = bucketEventsByHour(events, since);
    expect(buckets).toHaveLength(2);
    const b11 = buckets.find((b) => b.hour.endsWith('11:00'))!;
    expect(b11.critical).toBe(1);
    expect(b11.warning).toBe(1);
    expect(b11.total).toBe(2);
    const b12 = buckets.find((b) => b.hour.endsWith('12:00'))!;
    expect(b12.info).toBe(1);
  });
  it('since 이전 이벤트는 제외', () => {
    const events = [ev(1, 'critical', '2026-05-01T11:00:00Z')];
    const since = Date.parse('2026-06-01T00:00:00Z');
    expect(bucketEventsByHour(events, since)).toEqual([]);
  });
  it('빈 fired_at / 잘못된 ISO → skip', () => {
    const events = [ev(1, 'info', ''), ev(2, 'info', 'not-a-date')];
    const since = 0;
    expect(bucketEventsByHour(events, since)).toEqual([]);
  });
  it('정렬 — hour 오름차순', () => {
    const events = [
      ev(1, 'info', '2026-06-02T14:00:00Z'),
      ev(2, 'info', '2026-06-02T10:00:00Z'),
      ev(3, 'info', '2026-06-02T12:00:00Z'),
    ];
    const buckets = bucketEventsByHour(events, 0);
    expect(buckets.map((b) => b.hour.slice(-5))).toEqual(['10:00', '12:00', '14:00']);
  });
});
