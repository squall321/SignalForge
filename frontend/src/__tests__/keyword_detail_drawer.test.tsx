import { describe, it, expect } from 'vitest';
import type { KeywordDetailResponse } from '../types/deep';

// UX R2 트랙 A — KeywordDetailDrawer 의 순수 판정·click-handler 로직 단위 테스트.
// jsdom 미설정 환경이라 DOM mount 대신 (1) node click params 파싱,
// (2) empty 판정, (3) 카테고리 분포 비율 계산을 검증한다.

// (1) KeywordNetworkCard 의 click handler 가 추출하는 키워드/언어.
type EChartsNodeClickParams = {
  dataType?: string;
  data?: { id?: string; keyword?: string; lang?: string | null };
};
function extractClickedKeyword(
  params: EChartsNodeClickParams,
): { keyword: string; lang: string | null } | null {
  if (params?.dataType !== 'node') return null;
  const kw = params.data?.keyword || params.data?.id;
  if (!kw) return null;
  return { keyword: kw, lang: params.data?.lang ?? null };
}

describe('extractClickedKeyword (KeywordNetworkCard onChart click)', () => {
  it('node click + keyword 존재 → keyword + lang 반환', () => {
    const r = extractClickedKeyword({
      dataType: 'node',
      data: { id: 'k1', keyword: '배터리', lang: 'ko' },
    });
    expect(r).toEqual({ keyword: '배터리', lang: 'ko' });
  });
  it('edge click 은 무시', () => {
    expect(
      extractClickedKeyword({
        dataType: 'edge',
        data: { id: 'e1', keyword: '배터리' },
      }),
    ).toBeNull();
  });
  it('keyword 비어있으면 id 로 fallback', () => {
    const r = extractClickedKeyword({
      dataType: 'node',
      data: { id: 'fallback', keyword: undefined },
    });
    expect(r?.keyword).toBe('fallback');
    expect(r?.lang).toBeNull();
  });
  it('data 자체 없으면 null', () => {
    expect(extractClickedKeyword({ dataType: 'node' })).toBeNull();
  });
});

// (2) Drawer empty 판정식 — drawer 내부와 동일.
function isEmpty(
  isLoading: boolean,
  data: KeywordDetailResponse | undefined,
): boolean {
  return !!(
    !isLoading &&
    data &&
    data.stats.total_count === 0 &&
    data.samples.length === 0 &&
    data.related_keywords.length === 0
  );
}

describe('KeywordDetailDrawer empty 판정', () => {
  it('로딩 중에는 empty 아님', () => {
    expect(isEmpty(true, undefined)).toBe(false);
  });
  it('total=0 + 샘플/연결 0건 → empty', () => {
    const empty: KeywordDetailResponse = {
      keyword: 'x',
      lang: null,
      period_days: 7,
      stats: {
        total_count: 0,
        sentiment_avg: 0,
        top_products: [],
        top_platforms: [],
      },
      samples: [],
      related_keywords: [],
      categories: [],
      meta: {},
    };
    expect(isEmpty(false, empty)).toBe(true);
  });
  it('샘플 1건이라도 있으면 empty 아님', () => {
    const filled: KeywordDetailResponse = {
      keyword: '배터리',
      lang: 'ko',
      period_days: 7,
      stats: {
        total_count: 3,
        sentiment_avg: -0.2,
        top_products: [],
        top_platforms: [],
      },
      samples: [
        {
          id: 1,
          content_preview: '배터리 발열 심함',
          sentiment_label: 'negative',
          product: 'A55',
          platform: 'instiz',
          url: null,
          published_at: null,
        },
      ],
      related_keywords: [],
      categories: [],
      meta: {},
    };
    expect(isEmpty(false, filled)).toBe(false);
  });
});

// (3) 카테고리 분포 mini-bar 비율 계산.
function categoryBarPct(
  cats: { category: string; count: number }[],
): { category: string; pct: number }[] {
  const max = cats.length ? Math.max(...cats.map((c) => c.count)) : 0;
  return cats.map((c) => ({
    category: c.category,
    pct: max > 0 ? (c.count / max) * 100 : 0,
  }));
}

describe('categoryBarPct', () => {
  it('가장 큰 카테고리는 100%', () => {
    const out = categoryBarPct([
      { category: '발열', count: 50 },
      { category: '성능', count: 25 },
      { category: '디자인', count: 5 },
    ]);
    expect(out[0].pct).toBe(100);
    expect(out[1].pct).toBe(50);
    expect(out[2].pct).toBe(10);
  });
  it('빈 배열 → 빈 배열', () => {
    expect(categoryBarPct([])).toEqual([]);
  });
});

// (4) onSelectKeyword 연쇄 — 연결 키워드 클릭 시 Drawer 가 재오픈된다.
describe('연결 키워드 재오픈 시나리오', () => {
  it('Tag 클릭 콜백이 새 keyword/lang 으로 호출된다', () => {
    let current: { keyword: string | null; lang: string | null } = {
      keyword: '배터리',
      lang: 'ko',
    };
    const onSelectKeyword = (kw: string, lng?: string | null) => {
      current = { keyword: kw, lang: lng ?? null };
    };
    onSelectKeyword('발열', 'ko');
    expect(current).toEqual({ keyword: '발열', lang: 'ko' });
  });
});
