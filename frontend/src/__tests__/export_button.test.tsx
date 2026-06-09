// R10 트랙 E — ExportButton 순수 함수 단위 테스트.
//
// 환경: vitest node (jsdom 없음) — DOM mount 없이 export 함수만 검증.
// 1) buildExportFilename — series + format → 'voc_<series>_YYYY-MM-DD.<ext>'
// 2) parseFilenameHeader — Content-Disposition 헤더 파싱
// 3) buildExportMenuItems — 3 항목 (csv/excel/pdf) 정확
// 4) triggerExport — api blob 응답을 받아 download anchor 트리거

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  buildExportFilename,
  parseFilenameHeader,
  buildExportMenuItems,
  triggerExport,
} from '../components/global/ExportButton';

// api 모듈 mock — get/post 호출 가로채기
vi.mock('../services/api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import api from '../services/api';

describe('ExportButton — 순수 함수', () => {
  it('buildExportFilename: GS25 + csv → voc_GS25_YYYY-MM-DD.csv', () => {
    const name = buildExportFilename('GS25', 'csv');
    expect(name).toMatch(/^voc_GS25_\d{4}-\d{2}-\d{2}\.csv$/);
  });

  it('buildExportFilename: GZF1 + excel → .xlsx 확장자', () => {
    const name = buildExportFilename('GZF1', 'excel');
    expect(name.endsWith('.xlsx')).toBe(true);
  });

  it('buildExportFilename: GS + pdf → .pdf 확장자', () => {
    const name = buildExportFilename('GS', 'pdf');
    expect(name.endsWith('.pdf')).toBe(true);
    expect(name.startsWith('voc_GS_')).toBe(true);
  });

  it('parseFilenameHeader: 헤더 있음 → 파싱', () => {
    const f = parseFilenameHeader(
      'attachment; filename="voc_GS25_2026-06-04.csv"',
      'fallback.csv',
    );
    expect(f).toBe('voc_GS25_2026-06-04.csv');
  });

  it('parseFilenameHeader: 헤더 없음 → fallback', () => {
    expect(parseFilenameHeader(undefined, 'fallback.xlsx')).toBe('fallback.xlsx');
    expect(parseFilenameHeader('attachment', 'fallback.csv')).toBe('fallback.csv');
  });

  it('buildExportMenuItems: 3 항목 (csv / excel / pdf)', () => {
    const items = buildExportMenuItems();
    expect(items).toHaveLength(3);
    const keys = items.map((i) => (i as { key: string }).key);
    expect(keys).toEqual(['csv', 'excel', 'pdf']);
  });
});

describe('ExportButton — triggerExport mock', () => {
  // jsdom 없음 — document/URL 전역 모킹
  let originalDocument: Document | undefined;
  let originalURL: typeof URL | undefined;
  const clickSpy = vi.fn();
  const removeSpy = vi.fn();
  const createObjectURLSpy = vi.fn(() => 'blob:mock');
  const revokeObjectURLSpy = vi.fn();

  beforeEach(() => {
    clickSpy.mockReset();
    removeSpy.mockReset();
    createObjectURLSpy.mockClear();
    revokeObjectURLSpy.mockClear();
    (api.get as ReturnType<typeof vi.fn>).mockReset();
    (api.post as ReturnType<typeof vi.fn>).mockReset();

    originalDocument = (globalThis as { document?: Document }).document;
    originalURL = (globalThis as { URL?: typeof URL }).URL;
    const fakeAnchor = {
      href: '',
      download: '',
      click: clickSpy,
    } as unknown as HTMLAnchorElement;
    const fakeDoc = {
      createElement: vi.fn(() => fakeAnchor),
      body: {
        appendChild: vi.fn(() => fakeAnchor),
        removeChild: removeSpy,
      },
    } as unknown as Document;
    (globalThis as { document?: Document }).document = fakeDoc;
    (globalThis as { URL?: unknown }).URL = {
      createObjectURL: createObjectURLSpy,
      revokeObjectURL: revokeObjectURLSpy,
    };
  });

  afterEach(() => {
    if (originalDocument === undefined) {
      delete (globalThis as { document?: Document }).document;
    } else {
      (globalThis as { document?: Document }).document = originalDocument;
    }
    if (originalURL === undefined) {
      delete (globalThis as { URL?: typeof URL }).URL;
    } else {
      (globalThis as { URL?: typeof URL }).URL = originalURL;
    }
  });

  it('triggerExport csv → api.get(/_internal/export, type=csv) + anchor click', async () => {
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: new Blob(['id,collected_at\n1,2026'], { type: 'text/csv' }),
      headers: { 'content-disposition': 'attachment; filename="voc_GS25_2026-06-04.csv"' },
    });
    const r = await triggerExport('csv', { series: 'GS25', periodDays: 30 });
    expect(r.ok).toBe(true);
    expect(r.filename).toBe('voc_GS25_2026-06-04.csv');
    expect(api.get).toHaveBeenCalledWith(
      '/_internal/export',
      expect.objectContaining({
        params: { type: 'csv', series: 'GS25', period_days: 30 },
        responseType: 'blob',
      }),
    );
    expect(createObjectURLSpy).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalledTimes(1);
  });

  it('triggerExport pdf → api.post(/_internal/export-pdf) + sections 기본 4종', async () => {
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: new Blob(['%PDF-1.4'], { type: 'application/pdf' }),
      headers: { 'content-disposition': 'attachment; filename="voc_GS25_2026-06-04.pdf"' },
    });
    const r = await triggerExport('pdf', { series: 'GS25', periodDays: 7 });
    expect(r.ok).toBe(true);
    expect(r.filename).toBe('voc_GS25_2026-06-04.pdf');
    expect(api.post).toHaveBeenCalledWith(
      '/_internal/export-pdf',
      {
        product: 'GS25',
        period_days: 7,
        sections: ['kpi', 'timeline', 'categories', 'keywords'],
      },
      expect.objectContaining({ responseType: 'blob' }),
    );
    expect(clickSpy).toHaveBeenCalledTimes(1);
  });

  it('triggerExport 실패 시 ok=false + fallback filename', async () => {
    (api.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('500'));
    const r = await triggerExport('csv', { series: 'GS25', periodDays: 30 });
    expect(r.ok).toBe(false);
    expect(r.filename).toMatch(/^voc_GS25_\d{4}-\d{2}-\d{2}\.csv$/);
    expect(clickSpy).not.toHaveBeenCalled();
  });
});
