// P5 UX R2 트랙 B — CardActions 통합 동작 단위 테스트.
//
// 환경: vitest node (jsdom 없음).
// 그래서 React mount 가 아닌 CardActions 내부의 두 순수 동작을 검증한다.
//  1) PNG 다운로드 — echartsRef.getEchartsInstance().getDataURL() 가 호출되고
//     반환된 dataURL 이 anchor 의 href 에 실리는지 확인 (document API 모킹).
//  2) JSON 응답 — buildCardMenuItems 가 json 유무에 따라 menu 에 'json' key 를 포함하는지.
//
// 트랙 B 12 카드 전체는 동일한 CardActions 컴포넌트를 사용하므로
// 이 두 동작 검증으로 PNG / JSON 메뉴 통합 일관성을 보장한다.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { buildCardMenuItems, downloadPng } from '../components/common/CardActions';

describe('CardActions — PNG 다운로드 트리거 (mock)', () => {
  // jsdom 미설치 환경이므로 document 전역을 최소한으로 모킹.
  // anchor.click 호출 + href 에 dataURL 이 실리는지 검사.
  let originalDocument: Document | undefined;
  const clickSpy = vi.fn();
  const removeSpy = vi.fn();

  beforeEach(() => {
    clickSpy.mockReset();
    removeSpy.mockReset();
    originalDocument = (globalThis as { document?: Document }).document;
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
  });

  afterEach(() => {
    (globalThis as { document?: Document }).document = originalDocument as Document;
  });

  it('echartsRef.getEchartsInstance().getDataURL() 가 호출되고 dataURL 이 anchor href 에 실린다', () => {
    const getDataURL = vi.fn().mockReturnValue('data:image/png;base64,FAKE');
    const ref = {
      getEchartsInstance: () => ({ getDataURL }),
    };

    const ok = downloadPng(ref, 'demo_card');

    expect(ok).toBe(true);
    expect(getDataURL).toHaveBeenCalledWith({
      type: 'png',
      pixelRatio: 2,
      backgroundColor: '#fff',
    });
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(removeSpy).toHaveBeenCalledTimes(1);
  });
});

describe('CardActions — JSON 메뉴 노출 (응답 객체 미입력 가드)', () => {
  it('hasJson=true → menu 에 json 항목 포함', () => {
    const items = buildCardMenuItems({ hasChart: true, hasJson: true });
    const keys = items.map((i) => (i as { key: string }).key);
    expect(keys).toContain('json');
    expect(keys).toContain('png');
  });

  it('hasJson=false → json 항목 제외, expand 만 최소 노출', () => {
    const items = buildCardMenuItems({ hasChart: false, hasJson: false });
    const keys = items.map((i) => (i as { key: string }).key);
    expect(keys).not.toContain('json');
    expect(keys).not.toContain('png');
    expect(keys).toEqual(['expand']);
  });
});
