// P4.1 트랙 B — AnomalyDrilldownDrawer 순수 변환 헬퍼.
// DOM 의존 없는 로직만 분리하여 vitest 로 검증 가능하게 한다.
import type { DrilldownHourBucket } from '../../types/deep';

/** 24시간 중 count 가 가장 큰 hour 의 index. 동률은 먼저 등장하는 값. 빈 배열 → -1. */
export function peakHour(hourly: DrilldownHourBucket[]): number {
  if (!hourly.length) return -1;
  let best = 0;
  for (let i = 1; i < hourly.length; i += 1) {
    if (hourly[i].count > hourly[best].count) best = i;
  }
  return hourly[best].hour;
}

/** echarts bar series 용 [hour, count] 페어 + peak 강조 색상 컬렉션. */
export function buildHourlyBars(hourly: DrilldownHourBucket[]): {
  categories: string[];
  values: { value: number; itemStyle: { color: string } }[];
} {
  const peak = peakHour(hourly);
  return {
    categories: hourly.map((h) => `${h.hour}h`),
    values: hourly.map((h) => ({
      value: h.count,
      itemStyle: { color: h.hour === peak ? '#cf1322' : '#1677ff' },
    })),
  };
}
