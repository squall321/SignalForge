import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Card, Segmented, Slider, Space, Spin, Typography } from 'antd';
import { CaretRightOutlined, PauseOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { fetchDiffusion } from '../../services/geoApi';
import { useFilterStore } from '../../stores/useFilterStore';
import type { ChoroplethMode } from '../../types/geo';

const { Text } = Typography;

interface Props {
  // 현재 frame 의 country→value 맵을 부모(WorldChoropleth)에게 알려준다
  onFrameChange?: (values: Record<string, number>, date: string) => void;
  // 메트릭 모드를 외부와 동기화하고 싶을 때
  metric?: ChoroplethMode;
  onMetricChange?: (m: ChoroplethMode) => void;
  intervalMs?: number;   // 프레임 간격 (기본 600ms)
}

export default function DiffusionPlayer({
  onFrameChange,
  metric: metricProp,
  onMetricChange,
  intervalMs = 600,
}: Props) {
  const { dateRange, products } = useFilterStore();
  const [metricLocal, setMetricLocal] = useState<ChoroplethMode>('count');
  const metric = metricProp ?? metricLocal;
  const setMetric = (m: ChoroplethMode) => {
    if (onMetricChange) onMetricChange(m);
    else setMetricLocal(m);
  };

  const { data, isLoading } = useQuery({
    queryKey: ['diffusion', metric, dateRange.start, dateRange.end, products.join(',')],
    queryFn: () =>
      fetchDiffusion(metric, {
        start: dateRange.start,
        end: dateRange.end,
        products,
      }),
    staleTime: 60_000,
  });

  const frames = data?.frames ?? [];
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const timerRef = useRef<number | null>(null);

  // 데이터 바뀌면 idx 리셋
  useEffect(() => {
    setIdx(0);
    setPlaying(false);
  }, [data]);

  // 현재 frame 통지
  useEffect(() => {
    const f = frames[idx];
    if (f && onFrameChange) onFrameChange(f.values, f.date);
  }, [idx, frames, onFrameChange]);

  // 재생 타이머
  useEffect(() => {
    if (!playing) {
      if (timerRef.current) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
      return;
    }
    timerRef.current = window.setInterval(() => {
      setIdx((prev) => {
        if (prev >= frames.length - 1) {
          setPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, intervalMs);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [playing, frames.length, intervalMs]);

  const marks = useMemo(() => {
    if (frames.length === 0) return {};
    const m: Record<number, string> = {};
    m[0] = frames[0].date.slice(5);
    m[frames.length - 1] = frames[frames.length - 1].date.slice(5);
    return m;
  }, [frames]);

  const currentDate = frames[idx]?.date ?? '-';

  return (
    <Card
      title="확산 재생"
      bodyStyle={{ padding: 12 }}
      extra={
        <Segmented
          size="small"
          value={metric}
          options={[
            { label: '건수', value: 'count' },
            { label: '감성 z', value: 'sent_z' },
          ]}
          onChange={(v) => setMetric(v as ChoroplethMode)}
        />
      }
    >
      {isLoading && (
        <div style={{ textAlign: 'center', padding: 12 }}>
          <Spin size="small" />
        </div>
      )}
      {!isLoading && frames.length === 0 && (
        <Text type="secondary">데이터가 없습니다.</Text>
      )}
      {!isLoading && frames.length > 0 && (
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          <Space>
            <Button
              type="primary"
              icon={playing ? <PauseOutlined /> : <CaretRightOutlined />}
              onClick={() => setPlaying((p) => !p)}
              size="small"
            >
              {playing ? '일시정지' : '재생'}
            </Button>
            <Text strong>{currentDate}</Text>
            <Text type="secondary">
              ({idx + 1}/{frames.length})
            </Text>
          </Space>
          <Slider
            min={0}
            max={Math.max(0, frames.length - 1)}
            value={idx}
            onChange={(v) => setIdx(v as number)}
            tooltip={{ formatter: (v) => frames[v ?? 0]?.date }}
            marks={marks}
          />
        </Space>
      )}
    </Card>
  );
}
