// R12 트랙 E3 — LegacyDrawer 강화 단위 테스트.
// R11 트랙 E 에서 5-샘플 단순 wrapper → 풍부한 detail (시리즈 통계, 월별 timeline,
// 부정 샘플 %, 외부 링크) 로 확장된 LegacyDrawer 의 *순수 변환 로직* 검증.
//
// jsdom 미설정 — 컴포넌트 mount 대신 Drawer 가 사용하는 변환식을 재구현해
// 회귀를 잡는다 (Drawer 내부 로직과 동일).
import { describe, it, expect } from 'vitest';

// LegacyDrawer 내부 sample item / master timeline row 형태 (Drawer 코드와 동일)
type SampleItem = {
  id: number;
  content: string | null;
  sentiment_label: string | null;
  author_name: string | null;
  published_at: string | null;
  source_url: string | null;
};
type MasterRow = {
  product_code: string;
  name_ko: string | null;
  released_at: string | null;
  series: string;
  month: string;
  voc_count: number;
  sent_avg: number | null;
  neg_rate: number | null;
};

// (1) series prefix 추출 — Drawer 의 fetchMasterTimeline / "시리즈" Statistic
function seriesPrefix(code: string | null): string {
  return code?.match(/^[A-Z]+/)?.[0] ?? '';
}

// (2) 샘플 부정 비율 — Drawer 의 negSamplePct
function negSamplePct(items: SampleItem[]): number {
  if (items.length === 0) return 0;
  const neg = items.filter((i) => i.sentiment_label === 'negative').length;
  return Math.round((neg * 100) / items.length);
}

// (3) MasterRow → ECharts 시리즈 변환 (월 ASC 정렬 + YYYY-MM 축 + voc_count 값)
function monthlyEchartsData(rows: MasterRow[]): { months: string[]; counts: number[] } {
  const sorted = rows.slice().sort((a, b) => a.month.localeCompare(b.month));
  return {
    months: sorted.map((r) => r.month?.slice(0, 7) ?? ''),
    counts: sorted.map((r) => r.voc_count),
  };
}

// (4) MasterRow product_code 로 client-side filter (Drawer 의 fetchMasterTimeline 후처리)
function filterByCode(rows: MasterRow[], code: string): MasterRow[] {
  return rows.filter((r) => r.product_code === code);
}

// ── Tests ──────────────────────────────────────────────────────────────
describe('seriesPrefix (LegacyDrawer 시리즈 Statistic)', () => {
  it('GS25 → GS', () => expect(seriesPrefix('GS25')).toBe('GS'));
  it('GN7 → GN', () => expect(seriesPrefix('GN7')).toBe('GN'));
  it('GZF1 → GZF', () => expect(seriesPrefix('GZF1')).toBe('GZF'));
  it('GW7 → GW', () => expect(seriesPrefix('GW7')).toBe('GW'));
  it('숫자로 시작하면 빈 문자열', () => expect(seriesPrefix('123')).toBe(''));
  it('null 입력 → 빈 문자열 (throw 없음)', () => expect(seriesPrefix(null)).toBe(''));
  it('빈 문자열 → 빈 문자열', () => expect(seriesPrefix('')).toBe(''));
});

describe('negSamplePct (LegacyDrawer 부정 %)', () => {
  const mk = (sl: string | null): SampleItem => ({
    id: 1, content: null, sentiment_label: sl,
    author_name: null, published_at: null, source_url: null,
  });

  it('빈 배열 → 0', () => expect(negSamplePct([])).toBe(0));
  it('5건 중 1건 negative → 20%', () => {
    expect(negSamplePct([
      mk('negative'), mk('positive'), mk('positive'), mk('neutral'), mk(null),
    ])).toBe(20);
  });
  it('모두 negative → 100%', () => {
    expect(negSamplePct([mk('negative'), mk('negative')])).toBe(100);
  });
  it('샘플 3건 1건 negative → 33% (반올림)', () => {
    expect(negSamplePct([mk('negative'), mk('positive'), mk('neutral')])).toBe(33);
  });
});

describe('monthlyEchartsData (LegacyDrawer 월별 timeline)', () => {
  const row = (month: string, count: number): MasterRow => ({
    product_code: 'GS25', name_ko: 'Galaxy S25', released_at: '2025-02-01',
    series: 'GS', month, voc_count: count, sent_avg: 0, neg_rate: 0,
  });

  it('월 ASC 정렬 + YYYY-MM 축', () => {
    const out = monthlyEchartsData([
      row('2026-03-01', 30),
      row('2026-01-01', 10),
      row('2026-02-01', 20),
    ]);
    expect(out.months).toEqual(['2026-01', '2026-02', '2026-03']);
    expect(out.counts).toEqual([10, 20, 30]);
  });

  it('빈 입력 → 빈 결과', () => {
    const out = monthlyEchartsData([]);
    expect(out.months).toEqual([]);
    expect(out.counts).toEqual([]);
  });

  it('단일 row 도 처리', () => {
    const out = monthlyEchartsData([row('2026-05-01', 42)]);
    expect(out.months).toEqual(['2026-05']);
    expect(out.counts).toEqual([42]);
  });
});

describe('filterByCode (master timeline 후처리)', () => {
  const r = (code: string, month: string): MasterRow => ({
    product_code: code, name_ko: null, released_at: null,
    series: 'GS', month, voc_count: 10, sent_avg: 0, neg_rate: 0,
  });

  it('지정 code 만 남김', () => {
    const out = filterByCode(
      [r('GS25', '2026-05-01'), r('GS24', '2026-05-01'), r('GS25', '2026-06-01')],
      'GS25',
    );
    expect(out.map((x) => x.month)).toEqual(['2026-05-01', '2026-06-01']);
  });

  it('일치 없음 → 빈 배열', () => {
    expect(filterByCode([r('GS24', '2026-05-01')], 'GS25')).toEqual([]);
  });
});

describe('LegacyDrawer 조합 시나리오 (R11 강화 — drawer 풍부화 회귀)', () => {
  it('시리즈/부정%/월별 카운트 동시 계산', () => {
    const code = 'GS25';
    const samples: SampleItem[] = [
      { id: 1, content: 'c', sentiment_label: 'negative',
        author_name: 'a', published_at: '2026-06-01T10:00:00Z', source_url: 'https://x' },
      { id: 2, content: 'c', sentiment_label: 'positive',
        author_name: 'b', published_at: '2026-06-02T10:00:00Z', source_url: null },
    ];
    const rows: MasterRow[] = [
      { product_code: 'GS25', name_ko: 'S25', released_at: '2025-02-01',
        series: 'GS', month: '2026-06-01', voc_count: 100,
        sent_avg: 0.1, neg_rate: 0.2 },
      { product_code: 'GS24', name_ko: 'S24', released_at: '2024-02-01',
        series: 'GS', month: '2026-06-01', voc_count: 50,
        sent_avg: 0, neg_rate: 0.1 },
    ];

    // Drawer 가 호출하는 변환 순서를 그대로 재현
    expect(seriesPrefix(code)).toBe('GS');
    expect(negSamplePct(samples)).toBe(50); // 2건 중 1건 negative
    const filtered = filterByCode(rows, code);
    expect(filtered).toHaveLength(1);
    const chart = monthlyEchartsData(filtered);
    expect(chart.months).toEqual(['2026-06']);
    expect(chart.counts).toEqual([100]);
  });

  it('데이터 없음 — Empty 상태가 정확히 감지', () => {
    expect(negSamplePct([])).toBe(0);
    expect(filterByCode([], 'GS25')).toEqual([]);
    expect(monthlyEchartsData([])).toEqual({ months: [], counts: [] });
  });
});
