// KG 노드 검색 (디바운스 300ms) — P2-3 T1
import { useEffect, useMemo, useRef, useState } from 'react';
import { AutoComplete, Tag } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { fetchKGSearch } from '../../services/kgApi';
import { NODE_TYPE_COLOR, type KGNodeType } from '../../types/kg';

export interface KGSearchInputProps {
  onSelectNode: (nodeId: string) => void;
}

function useDebounced(value: string, ms: number): string {
  const [debounced, setDebounced] = useState(value);
  const timer = useRef<number | null>(null);
  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setDebounced(value), ms);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [value, ms]);
  return debounced;
}

export default function KGSearchInput({ onSelectNode }: KGSearchInputProps) {
  const [text, setText] = useState('');
  const q = useDebounced(text, 300);

  const { data } = useQuery({
    queryKey: ['kg', 'search', q],
    queryFn: () => fetchKGSearch(q),
    enabled: q.trim().length >= 2,
    staleTime: 30_000,
    retry: 0,
  });

  const options = useMemo(() => {
    return (data?.hits ?? []).map((h) => ({
      value: h.id,
      label: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <Tag color={NODE_TYPE_COLOR[h.type as KGNodeType]} style={{ marginRight: 0 }}>
            {h.type}
          </Tag>
          <span>{h.label}</span>
          <span style={{ color: '#999', marginLeft: 'auto' }}>({h.count})</span>
        </span>
      ),
    }));
  }, [data]);

  return (
    <AutoComplete
      style={{ width: 280 }}
      value={text}
      options={options}
      placeholder="노드 검색 (예: GS25U, battery...)"
      onChange={(v) => setText(v)}
      onSelect={(v) => {
        onSelectNode(v as string);
        setText('');
      }}
      allowClear
    />
  );
}
