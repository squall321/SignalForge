import { describe, it, expect } from 'vitest';
import { buildColorScale, indexByCountry } from './colorScale';
import { buildOption } from './ProductCompareBar';
import type { CountryMetric, ProductCompareItem } from '../../types/geo';

const SAMPLE: CountryMetric[] = [
  { country_code: 'USA', country_name: 'United States', count: 800, sent_avg: 0.3, sent_z: 1.2 },
  { country_code: 'KOR', country_name: 'Korea', count: 400, sent_avg: 0.1, sent_z: 0.0 },
  { country_code: 'JPN', country_name: 'Japan', count: 200, sent_avg: -0.2, sent_z: -0.8 },
];

describe('buildColorScale', () => {
  it('count mode 도메인은 [0, max(count)] 이어야 한다', () => {
    const scale = buildColorScale(SAMPLE, 'count');
    expect(scale.mode).toBe('count');
    expect(scale.domain).toEqual([0, 800]);
    // 정상값 매핑 — 유효 hex 색상이어야 한다
    expect(scale.color(400)).toMatch(/^#[0-9a-f]{6}$/);
    // undefined / NaN → missing 색
    expect(scale.color(undefined)).toBe(scale.missing);
    expect(scale.color(Number.NaN)).toBe(scale.missing);
  });

  it('sent_z mode 는 -absMax ~ +absMax 발산 도메인', () => {
    const scale = buildColorScale(SAMPLE, 'sent_z');
    expect(scale.mode).toBe('sent_z');
    expect(scale.domain[0]).toBeLessThan(0);
    expect(scale.domain[1]).toBeGreaterThan(0);
    expect(scale.color(0)).toMatch(/^#[0-9a-f]{6}$/);
    expect(scale.color(undefined)).toBe(scale.missing);
  });

  it('빈 배열도 안전하게 처리한다', () => {
    const scale = buildColorScale([], 'count');
    expect(scale.domain[1]).toBeGreaterThan(0);
    expect(scale.color(0)).toMatch(/^#[0-9a-f]{6}$/);
  });
});

describe('indexByCountry', () => {
  it('country_code 기준으로 인덱싱한다', () => {
    const idx = indexByCountry(SAMPLE);
    expect(Object.keys(idx).sort()).toEqual(['JPN', 'KOR', 'USA']);
    expect(idx.USA.count).toBe(800);
  });
});

describe('ProductCompareBar.buildOption', () => {
  const items: ProductCompareItem[] = [
    { country_code: 'USA', product_code: 'GS25', sent_avg: 0.3, sent_ci_low: 0.2, sent_ci_high: 0.4, count: 500 },
    { country_code: 'KOR', product_code: 'GS25', sent_avg: -0.1, sent_ci_low: -0.2, sent_ci_high: 0.0, count: 300 },
    { country_code: 'JPN', product_code: 'GS25', sent_avg: 0.0, sent_ci_low: -0.05, sent_ci_high: 0.05, count: 150 },
  ];

  it('sent_avg 내림차순 정렬한 후 series를 구성한다', () => {
    const opt = buildOption(items);
    // bar series + custom error series
    const series = opt.series as any[];
    expect(series.length).toBe(2);
    expect(series[0].type).toBe('bar');
    expect(series[1].type).toBe('custom');
    // 첫 라벨이 가장 큰 sent_avg
    const yLabels = (opt.yAxis as any).data;
    expect(yLabels[0]).toBe('USA');
    // x축은 -1 ~ 1
    expect((opt.xAxis as any).min).toBe(-1);
    expect((opt.xAxis as any).max).toBe(1);
  });

  it('빈 입력에서 안전하게 동작', () => {
    const opt = buildOption([]);
    expect((opt.yAxis as any).data).toEqual([]);
    expect((opt.series as any[])[0].data).toEqual([]);
  });
});
