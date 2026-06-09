// P4.3 트랙 B — CardActions 메뉴 빌더 단위 테스트.
// jsdom 없이 순수 함수만 검증 (메뉴 항목 구성 + downloadPng 가드).
import { describe, it, expect } from 'vitest';
import { buildCardMenuItems, downloadPng } from '../components/common/CardActions';

describe('buildCardMenuItems', () => {
  it('chart + json 모두 있으면 png / json / expand 3개', () => {
    const items = buildCardMenuItems({ hasChart: true, hasJson: true });
    expect(items.map((i) => (i as { key: string }).key)).toEqual(['png', 'json', 'expand']);
  });

  it('json 만 있으면 PNG 항목 제거', () => {
    const items = buildCardMenuItems({ hasChart: false, hasJson: true });
    expect(items.map((i) => (i as { key: string }).key)).toEqual(['json', 'expand']);
  });

  it('아무것도 없으면 expand 만 노출 (최소 항목 보장)', () => {
    const items = buildCardMenuItems({ hasChart: false, hasJson: false });
    expect(items.map((i) => (i as { key: string }).key)).toEqual(['expand']);
  });
});

describe('downloadPng', () => {
  it('echartsRef 가 null → false 반환 (가드)', () => {
    expect(downloadPng(null, 'demo')).toBe(false);
    expect(downloadPng(undefined, 'demo')).toBe(false);
  });
});
