// R10 트랙 B — /history UX 강화 순수 유틸 단위 테스트.
// jsdom 미설정 환경이라 새 컴포넌트는 mount 하지 않고, historyApi 의
// R10 신규 헬퍼 (masterTimelineSeries / seriesHeatmapCells / MASTER_SERIES_SPECS) 만 검증.
import { describe, it, expect } from 'vitest';
import {
  MASTER_SERIES_SPECS,
  masterTimelineSeries,
  seriesHeatmapCells,
} from '../services/historyApi';
import type { GalaxyTimelineModel } from '../types/deep';

function mk(code: string, released: string | null, total: number): GalaxyTimelineModel {
  return {
    code,
    name: code,
    series: 'GS',
    released_at: released,
    voc_7d_count: 0,
    sent_avg: 0,
    neg_rate: 0,
    peak_count: 0,
    total_count: total,
  };
}

describe('MASTER_SERIES_SPECS — B1 master timeline 시리즈 색상 spec', () => {
  it('5종 시리즈 (S/Note/Z/Watch/Buds) 각각 key/label/color 보유', () => {
    expect(MASTER_SERIES_SPECS).toHaveLength(5);
    const keys = MASTER_SERIES_SPECS.map((s) => s.key).sort();
    expect(keys).toEqual(['Buds', 'Note', 'S', 'Watch', 'Z']);
    MASTER_SERIES_SPECS.forEach((s) => {
      expect(s.label.length).toBeGreaterThan(0);
      // 6자리 hex 색.
      expect(s.color).toMatch(/^#[0-9A-Fa-f]{6}$/);
    });
  });
});

describe('masterTimelineSeries — B1 누적 시계열 변환', () => {
  it('연도별 누적 합산이 연도 순서로 단조 증가 (또는 동일)', () => {
    const inputs = [
      {
        key: 'S',
        label: 'Galaxy S',
        color: '#0072B2',
        models: [
          mk('GS1', '2010-06-04', 100),
          mk('GS2', '2011-05-01', 200),
          mk('GS22U', '2022-02-25', 500),
        ],
      },
      {
        key: 'Note',
        label: 'Galaxy Note',
        color: '#E69F00',
        models: [
          mk('GN1', '2011-10-29', 50),
          mk('GN7', '2016-08-19', 300),
        ],
      },
    ];
    const { years, seriesData } = masterTimelineSeries(inputs, 2010, 2022);
    // 13 연도 (2010..2022).
    expect(years).toHaveLength(13);
    expect(years[0]).toBe(2010);
    expect(years[years.length - 1]).toBe(2022);
    // 시리즈 2개.
    expect(seriesData).toHaveLength(2);
    // S 누적: 2010=100, 2011=300, …, 2022=800.
    const s = seriesData[0];
    expect(s.name).toBe('Galaxy S');
    expect(s.values[0]).toBe(100);
    expect(s.values[1]).toBe(300);
    expect(s.values[s.values.length - 1]).toBe(800);
    // 단조 증가 (또는 동일) 검증.
    for (let i = 1; i < s.values.length; i++) {
      expect(s.values[i]).toBeGreaterThanOrEqual(s.values[i - 1]);
    }
    // Note 누적: 2011(idx 1)=50, 2015(idx 5)=50 (사이 0), 2016(idx 6)=350.
    const n = seriesData[1];
    expect(n.values[1]).toBe(50);
    expect(n.values[5]).toBe(50);
    expect(n.values[6]).toBe(350);
  });

  it('빈 모델 / null released_at → 전 구간 0 누적', () => {
    const { seriesData } = masterTimelineSeries(
      [{ key: 'S', label: 'S', color: '#000000', models: [mk('X', null, 999)] }],
      2010,
      2012,
    );
    expect(seriesData[0].values).toEqual([0, 0, 0]);
  });
});

describe('seriesHeatmapCells — B4 heatmap 변환', () => {
  it('행=세대(max), 열=시리즈, 셀=sent_avg 좌표', () => {
    const out = seriesHeatmapCells([
      {
        label: 'Galaxy S',
        points: [
          { gen: 1, sent_avg: 0.1 },
          { gen: 2, sent_avg: -0.2 },
          { gen: 3, sent_avg: 0.3 },
        ],
      },
      {
        label: 'Galaxy Note',
        points: [
          { gen: 1, sent_avg: 0.0 },
          { gen: 2, sent_avg: 0.4 },
        ],
      },
    ]);
    expect(out.cols).toEqual(['Galaxy S', 'Galaxy Note']);
    expect(out.rows).toEqual(['세대 1', '세대 2', '세대 3']);
    // 3 + 2 = 5 셀.
    expect(out.cells).toHaveLength(5);
    // S의 gen=2 → [col=0, row=1, -0.2]
    expect(out.cells).toContainEqual([0, 1, -0.2]);
    // Note의 gen=2 → [col=1, row=1, 0.4]
    expect(out.cells).toContainEqual([1, 1, 0.4]);
  });

  it('빈 입력 → 빈 rows/cols/cells', () => {
    const out = seriesHeatmapCells([]);
    expect(out.rows).toEqual([]);
    expect(out.cols).toEqual([]);
    expect(out.cells).toEqual([]);
  });
});
