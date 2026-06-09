// Track A — AlertMonitorPanel 의 순수 데이터 변환 단위 테스트.
// vitest 기본 node 환경 — DOM mount 없이 alertMonitorUtils 의 출력만 검증.
import { describe, it, expect } from 'vitest';
import type {
  AlertMonitorResponse,
  AlertMonitorRule,
} from '../services/alertsApi';
import {
  HEALTH_COLOR,
  HEALTH_LABEL,
  classifyRecommendation,
  ruleToSpark,
  summarizeHealth,
} from '../components/alerts/alertMonitorUtils';

function makeRule(over: Partial<AlertMonitorRule> = {}): AlertMonitorRule {
  return {
    rule_id: 3,
    name: 'new_term_spike',
    metric_path: 'insights.new_term_spike_count',
    threshold: 800,
    cooldown_sec: 3600,
    severity: 'warning',
    fires_24h: 350,
    fires_7d: 355,
    avg_value_7d: 610,
    max_value_7d: 940,
    last_fired_at: '2026-06-03T08:06:37+00:00',
    cooldown_violations_24h: 350,
    silent_window: false,
    health: 'violating',
    ...over,
  };
}

describe('HEALTH_COLOR / HEALTH_LABEL', () => {
  it('4 health 값 모두 정의', () => {
    for (const h of ['normal', 'silent', 'noisy', 'violating'] as const) {
      expect(typeof HEALTH_COLOR[h]).toBe('string');
      expect(typeof HEALTH_LABEL[h]).toBe('string');
    }
  });
});

describe('ruleToSpark', () => {
  it('fires_7d=0 → 7 개 0 점', () => {
    const r = makeRule({ fires_7d: 0, fires_24h: 0 });
    const pts = ruleToSpark(r);
    expect(pts).toHaveLength(7);
    expect(pts.every((p) => p === 0)).toBe(true);
  });
  it('fires_7d=355 → 7 개, 마지막은 fires_24h, 길이 7', () => {
    const r = makeRule({ fires_7d: 355, fires_24h: 350 });
    const pts = ruleToSpark(r);
    expect(pts).toHaveLength(7);
    expect(pts[6]).toBe(350);
    // 첫 점은 base*0.6 (= 355/7 * 0.6 ≈ 30.4)
    expect(pts[0]).toBeCloseTo((355 / 7) * 0.6, 5);
  });
  it('fires_24h=0 + fires_7d>0 → 마지막 점은 base 로 폴백', () => {
    const r = makeRule({ fires_7d: 70, fires_24h: 0 });
    const pts = ruleToSpark(r);
    expect(pts[6]).toBe(10);  // 70/7 = 10
  });
});

describe('classifyRecommendation', () => {
  it('"cooldown 위반" 포함 → error', () => {
    expect(
      classifyRecommendation(
        "rule 3 (`new_term_spike`) cooldown 위반 350건 — cooldown_sec=3600s 점검",
      ),
    ).toBe('error');
  });
  it('"임계 검토" 포함 → warning', () => {
    expect(
      classifyRecommendation(
        "rule 35 (`platforms_negative_share`) 임계 검토 — 7d silent (threshold=0.12)",
      ),
    ).toBe('warning');
  });
  it('해당 키워드 없음 → info', () => {
    expect(classifyRecommendation('기타 권고 문장')).toBe('info');
  });
});

describe('summarizeHealth', () => {
  it('null → 모든 카운트 0', () => {
    const s = summarizeHealth(null);
    expect(s).toEqual({ normal: 0, silent: 0, noisy: 0, violating: 0 });
  });

  it('3 룰 — violating 1 + silent 1 + violating 1 → 카운트 누적', () => {
    const data: AlertMonitorResponse = {
      days: 7,
      generated_at: '2026-06-03T15:47:51+00:00',
      summary: {
        active_rules: 3,
        fires_24h: 381,
        fires_7d: 386,
        cooldown_violations_24h: 374,
      },
      rules: [
        makeRule({ rule_id: 3, health: 'violating' }),
        makeRule({
          rule_id: 35,
          name: 'platforms_negative_share',
          fires_7d: 0,
          fires_24h: 0,
          avg_value_7d: null,
          max_value_7d: null,
          last_fired_at: null,
          cooldown_violations_24h: 0,
          silent_window: true,
          health: 'silent',
        }),
        makeRule({ rule_id: 37, name: 'platforms_negative_share_watch', health: 'violating' }),
      ],
      metric_distribution: {},
      recommendations: [],
    };
    const s = summarizeHealth(data);
    expect(s.violating).toBe(2);
    expect(s.silent).toBe(1);
    expect(s.normal).toBe(0);
    expect(s.noisy).toBe(0);
  });
});
