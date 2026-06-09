import { describe, it, expect } from 'vitest';
import type {
  AnomalyDrilldownResponse,
  DrilldownHourBucket,
} from '../types/deep';
import { buildHourlyBars, peakHour } from '../components/deep/anomalyDrilldownUtils';

// P4.1 트랙 B — AnomalyDrilldownDrawer 의 순수 변환 로직 단위 테스트.
// jsdom 미설정 환경이라 DOM mount 대신 데이터 흐름을 검증한다.
const HOURLY: DrilldownHourBucket[] = [
  { hour: 0, count: 10, sent_avg: 0.1, neg_rate: 0.05 },
  { hour: 1, count: 50, sent_avg: 0.0, neg_rate: 0.1 },
  { hour: 2, count: 30, sent_avg: -0.1, neg_rate: 0.2 },
];

describe('peakHour', () => {
  it('가장 큰 count 의 hour 반환', () => {
    expect(peakHour(HOURLY)).toBe(1);
  });
  it('빈 배열 → -1', () => {
    expect(peakHour([])).toBe(-1);
  });
});

describe('buildHourlyBars', () => {
  it('peak 시간만 빨강, 나머지는 파랑', () => {
    const { categories, values } = buildHourlyBars(HOURLY);
    expect(categories).toEqual(['0h', '1h', '2h']);
    expect(values.map((v) => v.itemStyle.color)).toEqual(['#1677ff', '#cf1322', '#1677ff']);
    expect(values.map((v) => v.value)).toEqual([10, 50, 30]);
  });
});

describe('Drawer empty/loading 판정', () => {
  // Drawer 내부 empty 판정식: isLoading=false && data 모든 섹션 0건.
  const isEmpty = (
    isLoading: boolean,
    data: AnomalyDrilldownResponse | undefined,
  ): boolean =>
    !!(!isLoading && data && !data.hourly.length && !data.products.length && !data.keywords.length);

  it('로딩 중에는 empty 아님', () => {
    expect(isEmpty(true, undefined)).toBe(false);
  });
  it('모든 섹션 0건 → empty', () => {
    const empty: AnomalyDrilldownResponse = {
      date: '2026-05-26',
      anomaly_summary: { z: 0, value: 0, baseline: 0 },
      hourly: [],
      products: [],
      keywords: [],
      platforms: [],
      meta: {},
    };
    expect(isEmpty(false, empty)).toBe(true);
  });
  it('hourly 1개라도 있으면 empty 아님', () => {
    const filled: AnomalyDrilldownResponse = {
      date: '2026-05-26',
      anomaly_summary: { z: 2.0, value: 100, baseline: 50 },
      hourly: HOURLY,
      products: [],
      keywords: [],
      platforms: [],
      meta: {},
    };
    expect(isEmpty(false, filled)).toBe(false);
  });
});
