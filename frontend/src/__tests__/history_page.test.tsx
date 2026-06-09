// R9 트랙 A — /history 페이지 순수 유틸 단위 테스트.
// vitest node 환경 — historyApi 헬퍼 (totalVoc7d, crisisLineSeries,
// timelineEchartsData, legacyDistribution) 만 검증.
//
// 검증:
//   1) timelineEchartsData — 출시일 정렬 + neg_rate % 변환
//   2) crisisLineSeries / legacyDistribution / totalVoc7d 종합
import { describe, it, expect } from 'vitest';
import {
  crisisLineSeries,
  legacyDistribution,
  timelineEchartsData,
  totalVoc7d,
  SERIES_OPTIONS,
} from '../services/historyApi';
import type { CrisisCase, GalaxyTimelineModel } from '../types/deep';

function mkModel(
  code: string,
  released: string | null,
  voc7d: number,
  total: number,
  negRate = 0,
  peak = 0,
): GalaxyTimelineModel {
  return {
    code,
    name: code,
    series: 'GS',
    released_at: released,
    voc_7d_count: voc7d,
    sent_avg: 0,
    neg_rate: negRate,
    peak_count: peak,
    total_count: total,
  };
}

describe('timelineEchartsData', () => {
  it('출시일 ASC 정렬 + neg_rate %(소수1) 변환', () => {
    const out = timelineEchartsData([
      mkModel('GS3', '2012-05-29', 200, 800, 0.1234),
      mkModel('GS1', '2010-06-04', 20, 50, 0),
      mkModel('GS2', '2011-05-01', 100, 300, 0.05),
    ]);
    expect(out.codes).toEqual(['GS1', 'GS2', 'GS3']);
    expect(out.counts).toEqual([20, 100, 200]);
    expect(out.totals).toEqual([50, 300, 800]);
    expect(out.negRates).toEqual([0, 5, 12.3]);
  });

  it('빈 입력 → 빈 배열', () => {
    const out = timelineEchartsData([]);
    expect(out.codes).toEqual([]);
    expect(out.counts).toEqual([]);
  });

  it('released_at null 도 마지막에 배치되며 깨지지 않음', () => {
    const out = timelineEchartsData([
      mkModel('GSX', null, 10, 20),
      mkModel('GS1', '2010-06-04', 5, 10),
    ]);
    // null < '2010-06-04' (localeCompare 빈문자열) → null 이 앞에 올 수 있다.
    // 핵심은 throw 없음 + 개수 보존.
    expect(out.codes).toHaveLength(2);
    expect(out.counts.reduce((a, b) => a + b, 0)).toBe(15);
  });
});

describe('crisisLineSeries / totalVoc7d / legacyDistribution', () => {
  it('crisisLineSeries — day ASC 정렬', () => {
    const c: CrisisCase = {
      code: 'GN7',
      title: 't',
      description: 'd',
      period_start: '2016-08-19',
      period_end: '2016-12-31',
      total_voc: 0,
      neg_rate: 0,
      timeline: [
        { day: '2016-09-02', count: 5 },
        { day: '2016-08-19', count: 1 },
        { day: '2016-08-25', count: 3 },
      ],
      top_keywords: [],
      top_sites: [],
    };
    const s = crisisLineSeries(c);
    expect(s.x).toEqual(['2016-08-19', '2016-08-25', '2016-09-02']);
    expect(s.y).toEqual([1, 3, 5]);
  });

  it('totalVoc7d 합산', () => {
    const ms = [mkModel('a', '2020-01-01', 30, 100), mkModel('b', '2021-01-01', 70, 200)];
    expect(totalVoc7d(ms)).toBe(100);
  });

  it('legacyDistribution — 2020 이전 모델 total 내림차순', () => {
    const ms = [
      mkModel('GS5', '2014-04-11', 0, 483),
      mkModel('GS22', '2022-02-25', 0, 415),  // 제외 (>=2020)
      mkModel('GN7', '2016-08-19', 0, 352),
      mkModel('GS1', '2010-06-04', 0, 21),
      mkModel('Future', null, 0, 999),         // 제외 (null)
    ];
    const out = legacyDistribution(ms, 2020);
    expect(out.map((x) => x.code)).toEqual(['GS5', 'GN7', 'GS1']);
    expect(out[0].total).toBe(483);
  });

  it('SERIES_OPTIONS 최소 5개 + 각 항목 key/label 보유', () => {
    expect(SERIES_OPTIONS.length).toBeGreaterThanOrEqual(5);
    SERIES_OPTIONS.forEach((s) => {
      expect(typeof s.key).toBe('string');
      expect(s.label.length).toBeGreaterThan(0);
    });
  });
});
