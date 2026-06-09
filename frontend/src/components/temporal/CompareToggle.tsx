import { Segmented } from 'antd';
import type { CompareMode } from '../../types/temporal';

interface Props {
  value: CompareMode;
  onChange: (mode: CompareMode) => void;
}

/**
 * 시계열 인사이트 — 비교 모드 토글
 *   - products   : 제품 간 비교 (예: GS25 vs GS26)
 *   - periods    : 동일 제품의 기간 비교 (YoY 등)
 *   - categories : 카테고리 추이 비교
 */
export default function CompareToggle({ value, onChange }: Props) {
  return (
    <Segmented
      value={value}
      onChange={(v) => onChange(v as CompareMode)}
      options={[
        { label: '제품 비교', value: 'products' },
        { label: '기간 비교', value: 'periods' },
        { label: '카테고리 비교', value: 'categories' },
      ]}
    />
  );
}
