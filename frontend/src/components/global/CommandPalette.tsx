// P4.2 R6 트랙 D — 글로벌 명령 팔레트 (⌘K / Ctrl+K).
// UX R2 트랙 E — 백엔드 통합 검색(/api/v1/_internal/search) 으로 정확도 보강.
// AntD Modal + Input + List. 입력 → filterEntries (client-side) +
// debounced backend fetch (300ms) → 결과 merge (백엔드 우선) →
// 선택 시 해당 페이지로 navigate + 필요 시 필터(setProducts/setPlatforms) 자동 적용.
import { useEffect, useMemo, useRef, useState } from 'react';
import type { InputRef } from 'antd';
import { Modal, Input, List, Tag, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useFilterStore } from '../../stores/useFilterStore';
import { useQuery } from '@tanstack/react-query';
import { fetchEmergingKeywords } from '../../services/insightsApi';
import { fetchGlobalSearch, type SearchResponse } from '../../services/searchApi';
import {
  PAGE_ENTRIES,
  filterEntries,
  type SearchEntry,
} from './commandPaletteUtils';

const { Text } = Typography;

// MVP 정적 메타 — 백엔드 /api/v1/products 가 전체 48개를 주지만 후보용으로 핵심만 노출.
// (필터바 옵션과 동기화하기 전까지 임시.)
const PRODUCT_ENTRIES: SearchEntry[] = [
  { kind: 'product', label: 'Galaxy S25', key: 'gs25 galaxy s25', path: '/dashboard', payload: { products: ['GS25'] } },
  { kind: 'product', label: 'Galaxy S25 Ultra', key: 'gs25u galaxy s25 ultra', path: '/dashboard', payload: { products: ['GS25U'] } },
  { kind: 'product', label: 'Galaxy Z Fold6', key: 'gzf6 fold6', path: '/dashboard', payload: { products: ['GZF6'] } },
  { kind: 'product', label: 'Galaxy Z Flip6', key: 'gzl6 flip6', path: '/dashboard', payload: { products: ['GZL6'] } },
];

const PLATFORM_ENTRIES: SearchEntry[] = [
  { kind: 'platform', label: 'Reddit', key: 'reddit', path: '/community', payload: { platforms: ['reddit'] } },
  { kind: 'platform', label: 'YouTube', key: 'youtube', path: '/community', payload: { platforms: ['youtube'] } },
  { kind: 'platform', label: 'X (Twitter)', key: 'x twitter', path: '/community', payload: { platforms: ['x'] } },
  { kind: 'platform', label: 'GSMArena', key: 'gsmarena', path: '/community', payload: { platforms: ['gsmarena'] } },
  { kind: 'platform', label: 'XDA', key: 'xda', path: '/community', payload: { platforms: ['xda'] } },
];

const CATEGORY_ENTRIES: SearchEntry[] = [
  { kind: 'category', label: '배터리', key: 'battery 배터리', path: '/insights' },
  { kind: 'category', label: '카메라', key: 'camera 카메라', path: '/insights' },
  { kind: 'category', label: '발열', key: 'heating 발열', path: '/insights' },
  { kind: 'category', label: '디스플레이', key: 'display 디스플레이', path: '/insights' },
  { kind: 'category', label: '소프트웨어', key: 'software 소프트웨어', path: '/insights' },
];

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function CommandPalette({ open, onClose }: Props) {
  const [query, setQuery] = useState('');
  const [activeIdx, setActiveIdx] = useState(0);
  const navigate = useNavigate();
  const { setProducts, setPlatforms } = useFilterStore();
  const inputRef = useRef<InputRef>(null);

  // 키워드 후보: 백엔드 emerging-keywords (TOP 30). modal open 시점에 한 번 fetch.
  const { data: kwData } = useQuery({
    queryKey: ['palette', 'keywords'],
    queryFn: () => fetchEmergingKeywords({ period_days: 7, top_n: 30 }),
    enabled: open,
    staleTime: 60_000,
  });

  const keywordEntries: SearchEntry[] = useMemo(() => {
    const list = kwData?.emerging ?? [];
    return list.map((k) => ({
      kind: 'keyword' as const,
      label: k.keyword,
      key: k.keyword,
      path: '/insights',
      payload: { keyword: k.keyword },
    }));
  }, [kwData]);

  // ─── 백엔드 통합 검색 (UX R2 트랙 E) ──────────────────────────────────────
  // query 300ms debounce → /_internal/search 호출. 빈 쿼리는 호출 자체 스킵.
  const [debouncedQuery, setDebouncedQuery] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  const { data: backendSearch } = useQuery<SearchResponse>({
    queryKey: ['palette', 'search', debouncedQuery],
    queryFn: () => fetchGlobalSearch(debouncedQuery, 15),
    enabled: open && debouncedQuery.length > 0,
    staleTime: 60_000,
  });

  // 백엔드 결과 → SearchEntry[] 로 변환. 백엔드가 우선이므로 client-side dedupe.
  const backendEntries: SearchEntry[] = useMemo(() => {
    if (!backendSearch) return [];
    const out: SearchEntry[] = [];
    for (const p of backendSearch.products) {
      out.push({
        kind: 'product',
        label: `${p.name_ko} (${p.code})`,
        key: `${p.code} ${p.name_ko}`.toLowerCase(),
        path: '/dashboard',
        payload: { products: [p.code] },
      });
    }
    for (const pl of backendSearch.platforms) {
      out.push({
        kind: 'platform',
        label: pl.name,
        key: `${pl.code} ${pl.name}`.toLowerCase(),
        path: '/community',
        payload: { platforms: [pl.code] },
      });
    }
    for (const c of backendSearch.categories) {
      out.push({
        kind: 'category',
        label: c.name_ko,
        key: `${c.code} ${c.name_ko}`.toLowerCase(),
        path: '/insights',
      });
    }
    for (const k of backendSearch.keywords) {
      out.push({
        kind: 'keyword',
        label: `${k.keyword} (${k.count})`,
        key: k.keyword.toLowerCase(),
        path: '/insights',
        payload: { keyword: k.keyword },
      });
    }
    return out;
  }, [backendSearch]);

  const staticEntries: SearchEntry[] = useMemo(
    () => [...PAGE_ENTRIES, ...PRODUCT_ENTRIES, ...PLATFORM_ENTRIES, ...CATEGORY_ENTRIES, ...keywordEntries],
    [keywordEntries],
  );

  // 결과 merge: 백엔드 결과를 먼저, 그 뒤 client-side filtered 결과를 추가하되 중복 제거.
  // 중복 키 = `${kind}:${path}:${label}` (label 가 카운트 포함이라 충돌 적음).
  const results = useMemo(() => {
    const clientFiltered = filterEntries(staticEntries, query, 12);
    if (backendEntries.length === 0) return clientFiltered;
    const seen = new Set<string>();
    const merged: SearchEntry[] = [];
    const dedupKey = (e: SearchEntry) =>
      `${e.kind}:${(e.payload?.products?.[0] ?? e.payload?.platforms?.[0] ?? e.payload?.keyword ?? e.label).toLowerCase()}`;
    for (const e of backendEntries) {
      const k = dedupKey(e);
      if (!seen.has(k)) {
        seen.add(k);
        merged.push(e);
      }
    }
    for (const e of clientFiltered) {
      const k = dedupKey(e);
      if (!seen.has(k)) {
        seen.add(k);
        merged.push(e);
      }
    }
    return merged.slice(0, 15);
  }, [backendEntries, staticEntries, query]);

  // open 변경 시 query 초기화 + 입력 포커스
  useEffect(() => {
    if (open) {
      setQuery('');
      setActiveIdx(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  // 결과 갯수 바뀔 때 activeIdx 보정
  useEffect(() => {
    if (activeIdx >= results.length) setActiveIdx(0);
  }, [results.length, activeIdx]);

  const handleSelect = (entry: SearchEntry) => {
    if (entry.payload?.products) setProducts(entry.payload.products);
    if (entry.payload?.platforms) setPlatforms(entry.payload.platforms);
    navigate(entry.path);
    onClose();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (results[activeIdx]) handleSelect(results[activeIdx]);
    }
  };

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      closable={false}
      destroyOnClose
      width={600}
      styles={{ body: { padding: 12 } }}
      data-testid="command-palette-modal"
    >
      <Input
        ref={inputRef}
        placeholder="페이지·제품·플랫폼·키워드 검색 (ESC 닫기, ↑↓ 이동, Enter 선택)"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={onKeyDown}
        size="large"
        data-testid="command-palette-input"
      />
      <List
        size="small"
        style={{ marginTop: 8, maxHeight: 360, overflowY: 'auto' }}
        dataSource={results}
        locale={{ emptyText: '결과 없음' }}
        renderItem={(entry, idx) => (
          <List.Item
            data-testid={`palette-result-${idx}`}
            style={{
              cursor: 'pointer',
              background: idx === activeIdx ? '#e6f4ff' : undefined,
              padding: '6px 12px',
              borderRadius: 4,
            }}
            onMouseEnter={() => setActiveIdx(idx)}
            onClick={() => handleSelect(entry)}
          >
            <Tag color={kindColor(entry.kind)} style={{ marginRight: 8 }}>
              {kindLabel(entry.kind)}
            </Tag>
            <Text>{entry.label}</Text>
          </List.Item>
        )}
      />
    </Modal>
  );
}

function kindColor(kind: SearchEntry['kind']): string {
  switch (kind) {
    case 'page': return 'blue';
    case 'product': return 'purple';
    case 'platform': return 'cyan';
    case 'category': return 'gold';
    case 'keyword': return 'green';
  }
}

function kindLabel(kind: SearchEntry['kind']): string {
  switch (kind) {
    case 'page': return '페이지';
    case 'product': return '제품';
    case 'platform': return '플랫폼';
    case 'category': return '카테고리';
    case 'keyword': return '키워드';
  }
}
