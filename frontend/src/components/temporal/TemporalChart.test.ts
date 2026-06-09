import { describe, it, expect } from 'vitest';
import { buildChartOption } from './TemporalChart';
import type {
  Changepoint,
  TemporalSeries,
  TimelineEvent,
} from '../../types/temporal';

describe('buildChartOption', () => {
  const series: TemporalSeries[] = [
    {
      key: 'GS25',
      label: 'Galaxy S25',
      data: [
        { date: '2026-01-01', count: 10, sent_avg: 0.1 },
        { date: '2026-01-02', count: 20, sent_avg: 0.2 },
      ],
    },
    {
      key: 'GS25U',
      label: 'Galaxy S25 Ultra',
      data: [
        { date: '2026-01-01', count: 30, sent_avg: -0.1 },
        { date: '2026-01-03', count: 40, sent_avg: 0.0 },
      ],
    },
  ];
  const events: TimelineEvent[] = [
    { date: '2026-01-02', title: 'S25 출시', category: 'launch' },
  ];
  const changepoints: Changepoint[] = [
    { date: '2026-01-02', series_key: 'GS25', delta: +10 },
  ];

  it('builds a dual-axis chart option with count + sentiment series', () => {
    const option = buildChartOption(series, events, changepoints);

    // x축에 두 series의 모든 날짜가 정렬되어 합쳐져야 한다
    expect((option.xAxis as any).data).toEqual([
      '2026-01-01',
      '2026-01-02',
      '2026-01-03',
    ]);

    // y축 두 개 (건수 + 감성)
    expect(Array.isArray(option.yAxis)).toBe(true);
    expect((option.yAxis as any[]).length).toBe(2);
    expect((option.yAxis as any[])[1].min).toBe(-1);
    expect((option.yAxis as any[])[1].max).toBe(1);

    // 시리즈 갯수 = 입력 series × 2 (count + sent_avg)
    const out = option.series as any[];
    expect(out.length).toBe(4);

    // 첫 series에만 markLine(events) 존재
    expect(out[0].markLine).toBeDefined();
    expect(out[2].markLine).toBeUndefined();

    // 변곡점 markPoint 가 GS25 의 count series에 존재
    expect(out[0].markPoint).toBeDefined();
    expect(out[0].markPoint.data[0].xAxis).toBe('2026-01-02');
  });

  it('handles empty series + events gracefully', () => {
    const option = buildChartOption([], [], []);
    expect((option.xAxis as any).data).toEqual([]);
    expect((option.series as any[]).length).toBe(0);
  });

  it('fills missing dates with null to keep series aligned', () => {
    const option = buildChartOption(series, [], []);
    const out = option.series as any[];
    // GS25U 는 '2026-01-02' 가 없으므로 null
    const gs25uCount = out[2].data;
    expect(gs25uCount).toEqual([30, null, 40]);
  });
});
