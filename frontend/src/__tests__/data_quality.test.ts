// Data Clean 2 — Track D4 단위 테스트.
// dataQualityApi 의 순수 유틸 4종 검증 (jsdom 미사용).
import { describe, it, expect } from 'vitest';
import {
  parseMatchPct,
  activeRatio,
  worstSorted,
  matchPctTone,
  dataGrowSites,
  highMatchSites,
  DATA_GROW_CODES,
  type DataQualityWorstSite,
} from '../services/dataQualityApi';

function mk(code: string, match_pct: string): DataQualityWorstSite {
  return { code, active: 100, mx_match: 30, match_pct };
}

describe('parseMatchPct', () => {
  it('문자열 → number', () => {
    expect(parseMatchPct('27.0')).toBe(27);
    expect(parseMatchPct('9.8')).toBe(9.8);
  });
  it('number 그대로 통과', () => {
    expect(parseMatchPct(42.5)).toBe(42.5);
  });
  it('null / undefined / NaN → 0', () => {
    expect(parseMatchPct(null)).toBe(0);
    expect(parseMatchPct(undefined)).toBe(0);
    expect(parseMatchPct('abc')).toBe(0);
  });
});

describe('activeRatio', () => {
  it('소수 1자리 % 반환', () => {
    expect(activeRatio(75953, 152138)).toBeCloseTo(49.9, 1);
    expect(activeRatio(1, 4)).toBe(25);
  });
  it('total=0 → 0', () => {
    expect(activeRatio(100, 0)).toBe(0);
  });
});

describe('worstSorted', () => {
  it('match_pct 오름차순 (가장 더러운 곳 먼저)', () => {
    const out = worstSorted([mk('a', '27.0'), mk('b', '9.8'), mk('c', '15.5')]);
    expect(out.map((s) => s.code)).toEqual(['b', 'c', 'a']);
  });
  it('원본 mutation 없음', () => {
    const src = [mk('a', '27.0'), mk('b', '9.8')];
    const out = worstSorted(src);
    expect(src[0].code).toBe('a');
    expect(out[0].code).toBe('b');
  });
});

describe('matchPctTone', () => {
  it('임계 구간 매핑', () => {
    expect(matchPctTone(5)).toBe('danger');
    expect(matchPctTone(19.9)).toBe('danger');
    expect(matchPctTone(20)).toBe('warning');
    expect(matchPctTone(34.9)).toBe('warning');
    expect(matchPctTone(35)).toBe('normal');
    expect(matchPctTone(59.9)).toBe('normal');
    expect(matchPctTone(60)).toBe('good');
    expect(matchPctTone(99)).toBe('good');
  });
});

// L7 — Data Grow R5 helpers
describe('dataGrowSites', () => {
  it('DATA_GROW_CODES 만 추출', () => {
    const sites = [
      mk('arxiv', '47.0'),
      mk('kaskus', '37.0'),
      mk('reddit_rss', '71.1'),
      mk('hackernews', '64.7'),
    ];
    const out = dataGrowSites(sites);
    expect(out.map((s) => s.code).sort()).toEqual(['arxiv', 'reddit_rss']);
  });
  it('빈 입력 → 빈 배열', () => {
    expect(dataGrowSites([])).toEqual([]);
  });
  it('DATA_GROW_CODES 11개 등록 확인', () => {
    expect(DATA_GROW_CODES.size).toBe(11);
    expect(DATA_GROW_CODES.has('arxiv')).toBe(true);
    expect(DATA_GROW_CODES.has('quora')).toBe(true);
    expect(DATA_GROW_CODES.has('notebookcheck')).toBe(true);
  });
});

describe('highMatchSites', () => {
  it('기본 threshold 90 이상만', () => {
    const sites = [
      mk('a', '95.0'),
      mk('b', '85.0'),
      mk('c', '90.0'),
      mk('d', '50.0'),
    ];
    const out = highMatchSites(sites);
    expect(out.map((s) => s.code).sort()).toEqual(['a', 'c']);
  });
  it('threshold 커스텀', () => {
    const sites = [mk('a', '75'), mk('b', '60'), mk('c', '50')];
    const out = highMatchSites(sites, 70);
    expect(out.map((s) => s.code)).toEqual(['a']);
  });
});
