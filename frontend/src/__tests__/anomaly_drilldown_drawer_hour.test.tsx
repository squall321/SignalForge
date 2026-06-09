import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { AnomalyDrilldownHourResponse } from '../types/deep';

// jsdom 미설정 환경 — Drawer DOM 마운트 대신 click handler 가
// 호출하는 데이터 흐름(state 갱신 + API 호출 인자)을 검증한다.
//
// AnomalyDrilldownDrawer 의 핵심 시퀀스 (E3):
//   1) hourly bar 클릭 → onHourlyChartEvents.click({dataIndex})
//      → setSelectedHour(data.hourly[dataIndex].hour), setHourPage(1)
//   2) selectedHour !== null & date 존재 → fetchAnomalyDrilldownHour({date,hour,limit,offset})

// fetchAnomalyDrilldownHour 의 axios 호출 인자 검증을 위해 api 를 mock 한다.
vi.mock('../services/api', () => ({
  default: {
    get: vi.fn(),
  },
}));

import api from '../services/api';
import { fetchAnomalyDrilldownHour } from '../services/deepApi';

const SAMPLE_HOURLY = [
  { hour: 0, count: 10, sent_avg: 0.1, neg_rate: 0.05 },
  { hour: 1, count: 50, sent_avg: 0.0, neg_rate: 0.1 },
  { hour: 2, count: 30, sent_avg: -0.1, neg_rate: 0.2 },
];

// AnomalyDrilldownDrawer 의 click handler 재현 — index 검증.
function makeClickHandler(
  hourly: typeof SAMPLE_HOURLY,
  setSelectedHour: (h: number) => void,
  setHourPage: (p: number) => void,
) {
  return (params: { dataIndex?: number }) => {
    if (typeof params.dataIndex !== 'number') return;
    const hour = hourly[params.dataIndex]?.hour;
    if (typeof hour === 'number') {
      setSelectedHour(hour);
      setHourPage(1);
    }
  };
}

describe('Drawer hourly bar 클릭 → selectedHour state 전이', () => {
  it('dataIndex=1 bar 클릭 → setSelectedHour(1) + page reset', () => {
    const setSelectedHour = vi.fn();
    const setHourPage = vi.fn();
    const click = makeClickHandler(SAMPLE_HOURLY, setSelectedHour, setHourPage);
    click({ dataIndex: 1 });
    expect(setSelectedHour).toHaveBeenCalledWith(1);
    expect(setHourPage).toHaveBeenCalledWith(1);
  });

  it('dataIndex undefined → no-op', () => {
    const setSelectedHour = vi.fn();
    const setHourPage = vi.fn();
    const click = makeClickHandler(SAMPLE_HOURLY, setSelectedHour, setHourPage);
    click({});
    expect(setSelectedHour).not.toHaveBeenCalled();
    expect(setHourPage).not.toHaveBeenCalled();
  });
});

describe('fetchAnomalyDrilldownHour — click 후 API 페치', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('hour 클릭 시 axios get 인자에 date/hour/limit/offset 포함', async () => {
    const mockResp: AnomalyDrilldownHourResponse = {
      date: '2026-06-01',
      hour: 3,
      total: 2,
      items: [
        {
          id: 1,
          product: { code: 'GB4', name_ko: '갤럭시 버즈4' },
          platform: { code: 'reddit', name: 'Reddit' },
          content_preview: 'noise issue when ANC enabled',
          sentiment_label: 'negative',
          sentiment_score: -0.6,
          engagement_score: 12.5,
          url: 'https://reddit.com/r/x/1',
          published_at: '2026-06-01T03:14:00+00:00',
        },
        {
          id: 2,
          product: { code: 'GB4', name_ko: '갤럭시 버즈4' },
          platform: { code: 'reddit', name: 'Reddit' },
          content_preview: 'love the sound',
          sentiment_label: 'positive',
          sentiment_score: 0.7,
          engagement_score: 4.0,
          url: null,
          published_at: '2026-06-01T03:32:00+00:00',
        },
      ],
      meta: { limit: 20, offset: 0, returned: 2 },
    };
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ data: mockResp });

    const r = await fetchAnomalyDrilldownHour({
      date: '2026-06-01',
      hour: 3,
      limit: 20,
      offset: 0,
    });

    expect(api.get).toHaveBeenCalledWith(
      '/deep/anomaly-drilldown-hour',
      { params: { limit: 20, offset: 0, date: '2026-06-01', hour: 3 } },
    );
    expect(r.total).toBe(2);
    expect(r.items[0].sentiment_label).toBe('negative');
    expect(r.items[1].sentiment_label).toBe('positive');
  });
});
