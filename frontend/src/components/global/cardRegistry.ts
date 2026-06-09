// P4.2 R6 트랙 D — 즐겨찾기 카드 메타데이터 registry.
// cardId 단일 진실 — 카드 컴포넌트와 dashboard FavoritesSection 가 공유한다.
// 신규 카드 추가 시 여기에만 등록하면 dashboard 에 즐겨찾기 표시되도록 한다.

export interface CardMeta {
  id: string;
  label: string;
  /** 해당 카드가 위치한 페이지 경로 (점프용) */
  path: string;
  /** 영역 라벨 (테이블 그룹용) */
  area: string;
}

export const CARD_REGISTRY: CardMeta[] = [
  // /insights (딥 인사이트)
  { id: 'hourly-pattern', label: '시간대 패턴', path: '/insights', area: '딥 인사이트' },
  { id: 'weekday-pattern', label: '요일 패턴', path: '/insights', area: '딥 인사이트' },
  { id: 'emerging-keywords', label: '신규 키워드', path: '/insights', area: '딥 인사이트' },
  { id: 'new-terms', label: '신조어 추적', path: '/insights', area: '딥 인사이트' },
  { id: 'sentiment-swing', label: '감성 스윙', path: '/insights', area: '딥 인사이트' },
  { id: 'lifecycle', label: '제품 라이프사이클', path: '/insights', area: '딥 인사이트' },
  { id: 'influence', label: '플랫폼 영향력', path: '/insights', area: '딥 인사이트' },
  // /insights deep cards
  { id: 'anomaly-driver', label: 'Anomaly 원인 분석', path: '/insights', area: '딥 인사이트' },
  { id: 'anomaly-context', label: 'Anomaly 맥락', path: '/insights', area: '딥 인사이트' },
  { id: 'keyword-network', label: '키워드 네트워크', path: '/insights', area: '딥 인사이트' },
  { id: 'keyword-cooccurrence', label: '키워드 동시출현', path: '/insights', area: '딥 인사이트' },
  { id: 'issue-lifecycle', label: '이슈 라이프사이클', path: '/insights', area: '딥 인사이트' },
  { id: 'lifecycle-funnel', label: '라이프사이클 퍼널', path: '/insights', area: '딥 인사이트' },
  { id: 'product-funnel', label: '제품 퍼널', path: '/insights', area: '딥 인사이트' },
  { id: 'category-momentum', label: '카테고리 모멘텀', path: '/insights', area: '딥 인사이트' },
  { id: 'category-product-matrix', label: '카테고리 × 제품', path: '/insights', area: '딥 인사이트' },
  { id: 'country-sentiment-gap', label: '국가 감성 격차', path: '/insights', area: '딥 인사이트' },
  { id: 'engagement-sentiment', label: '참여-감성 매트릭스', path: '/insights', area: '딥 인사이트' },
  { id: 'influence-rank', label: '영향력 랭킹', path: '/insights', area: '딥 인사이트' },
  { id: 'new-term-survival', label: '신조어 생존', path: '/insights', area: '딥 인사이트' },
  { id: 'site-diffusion', label: '사이트 확산', path: '/insights', area: '딥 인사이트' },
];

const idIndex = new Map(CARD_REGISTRY.map((c) => [c.id, c] as const));

export function lookupCardMeta(id: string): CardMeta | undefined {
  return idIndex.get(id);
}
