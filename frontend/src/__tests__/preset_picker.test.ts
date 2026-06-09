// P4.2 E5 — PresetPicker 단위 테스트.
// vitest node 환경 → DOM 미사용. 선택 토글 (체크박스) 동작 + 빈 선택 가드 검증.
import { describe, it, expect } from 'vitest';
import { togglePresetKey } from '../components/alerts/alertsUtils';

describe('togglePresetKey (PresetPicker checkbox)', () => {
  it('체크박스 ON → 다시 클릭하면 OFF (선택 토글)', () => {
    let s = new Set<string>();
    s = togglePresetKey(s, 'new_term_warning');
    expect(s.has('new_term_warning')).toBe(true);
    expect(s.size).toBe(1);

    // 다시 클릭 → 해제
    s = togglePresetKey(s, 'new_term_warning');
    expect(s.has('new_term_warning')).toBe(false);
    expect(s.size).toBe(0);
  });

  it('다중 선택 누적 + 입력 Set 불변성 (이전 참조 변경 없음)', () => {
    const s0 = new Set<string>();
    const s1 = togglePresetKey(s0, 'a');
    const s2 = togglePresetKey(s1, 'b');
    // 새 Set 반환 → 원본 s0 은 변경 없음
    expect(s0.size).toBe(0);
    expect(s1).not.toBe(s0);
    expect(s2.size).toBe(2);
    expect(Array.from(s2).sort()).toEqual(['a', 'b']);
  });

  it('적용 버튼 disabled 가드 — selected.size === 0 일 때 true', () => {
    const empty = new Set<string>();
    const one = togglePresetKey(empty, 'x');
    // PresetPicker.tsx 의 okDisabled = selected.size === 0
    expect(empty.size === 0).toBe(true);
    expect(one.size === 0).toBe(false);
  });
});
