// P4.3 트랙 B: GlobalFilterBar 옵션을 실 API 메타로 로드.
// products 는 /api/v1/products (활성만), platforms/regions/categories 는 정적 카탈로그
// (백엔드 전용 list 엔드포인트가 없으므로 도메인 상수로 유지).
//
// 카탈로그 변경은 1 라인 수정으로 가능하도록 export.
import { useQuery } from '@tanstack/react-query';
import api from '../services/api';

export interface ProductMeta {
  code: string;
  name: string;
}

export const REGION_CATALOG: Array<{ code: string; label: string }> = [
  { code: 'KR', label: '한국' },
  { code: 'US', label: '미국' },
  { code: 'EU', label: '유럽' },
  { code: 'CN', label: '중국' },
  { code: 'JP', label: '일본' },
  { code: 'SEA', label: '동남아' },
  { code: 'IN', label: '인도' },
];

export const PLATFORM_CATALOG: Array<{ code: string; label: string }> = [
  { code: 'reddit', label: 'Reddit' },
  { code: 'youtube', label: 'YouTube' },
  { code: 'x', label: 'X (Twitter)' },
  { code: 'gsmarena', label: 'GSMArena' },
  { code: 'xda', label: 'XDA' },
  { code: 'naver', label: 'Naver Cafe' },
  { code: 'dcinside', label: 'DCInside' },
  { code: 'instiz', label: 'Instiz' },
];

// VOC 카테고리 12종 (백엔드 voc.category enum 과 일치)
export const CATEGORY_CATALOG: Array<{ code: string; label: string }> = [
  { code: 'battery', label: '배터리' },
  { code: 'camera', label: '카메라' },
  { code: 'display', label: '디스플레이' },
  { code: 'performance', label: '성능' },
  { code: 'price', label: '가격' },
  { code: 'design', label: '디자인' },
  { code: 'software', label: '소프트웨어' },
  { code: 'durability', label: '내구성' },
  { code: 'network', label: '네트워크' },
  { code: 'audio', label: '오디오' },
  { code: 'ai', label: 'AI 기능' },
  { code: 'other', label: '기타' },
];

interface ProductRead {
  code: string;
  name: string;
}

export function useProductOptions() {
  return useQuery({
    queryKey: ['meta', 'products'],
    queryFn: async () => {
      try {
        const { data } = await api.get<ProductRead[]>('/products', { params: { is_active: true } });
        return data.map((p) => ({ code: p.code, name: p.name }));
      } catch {
        // 백엔드 미가동 시 폴백 — 데모 4종
        return [
          { code: 'GS25', name: 'Galaxy S25' },
          { code: 'GS25U', name: 'Galaxy S25 Ultra' },
          { code: 'GZF6', name: 'Galaxy Z Fold6' },
          { code: 'GZL6', name: 'Galaxy Z Flip6' },
        ];
      }
    },
    staleTime: 30 * 60_000,
  });
}
