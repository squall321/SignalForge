// P3.7 트랙 B — AnomalyDriverCard 의 순수 선택 로직.
// React/echarts 의존성을 제외하여 vitest 단위 테스트 대상으로 분리.
import type { AnomalyWithDriversEntry, TopDriver } from '../../types/deep';

/** 정렬: 날짜 오름차순 (timeline 표시용). */
export function sortByDate(items: AnomalyWithDriversEntry[]): AnomalyWithDriversEntry[] {
  return [...items].sort((a, b) => a.date.localeCompare(b.date));
}

/**
 * 선택된 날짜의 entry 를 반환. 없으면 z 최대값 entry 반환.
 * 빈 배열이면 null.
 */
export function selectEntry(
  items: AnomalyWithDriversEntry[],
  selectedDate: string | null,
): AnomalyWithDriversEntry | null {
  if (!items.length) return null;
  if (selectedDate) {
    const m = items.find((a) => a.date === selectedDate);
    if (m) return m;
  }
  return [...items].sort((a, b) => b.z - a.z)[0];
}

/** sentiment → bar 색상 (긍정 파랑 / 부정 빨강 / 중립 회색). */
export function driverColor(d: TopDriver): string {
  if (d.sentiment < -0.05) return '#cf1322';
  if (d.sentiment > 0.05) return '#1677ff';
  return '#8c8c8c';
}
