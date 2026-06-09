import { Card, Empty, List, Spin, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { fetchAnomalyContext } from '../../services/deepApi';
import CardActions from '../common/CardActions';

const { Text, Paragraph } = Typography;

// 차트 없는 리스트 카드 — chartTheme palette 는 적용하지 않고 (echarts 미사용),
// CardActions ⋮ 메뉴로 JSON 응답 보기 / 확대 보기만 제공한다.
export default function AnomalyContextCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'anomaly'],
    queryFn: () => fetchAnomalyContext({ period_days: 14, z_threshold: 2.5 }),
    staleTime: 5 * 60_000,
  });

  return (
    <Card
      title="이상치 맥락 추정 (spike + 키워드 delta + event 매칭)"
      size="small"
      bodyStyle={{ height: 320, padding: 8, overflow: 'auto' }}
      extra={
        <CardActions
          title="이상치 맥락"
          json={data}
        />
      }
    >
      {isLoading || !data ? (
        <Spin />
      ) : !data.spikes.length ? (
        <Empty description="spike 없음" />
      ) : (
        <List
          size="small"
          dataSource={data.spikes.slice(0, 8)}
          renderItem={(sp) => (
            <List.Item style={{ display: 'block', paddingBottom: 6 }}>
              <div>
                <Tag color="red">{sp.date}</Tag>
                <Tag color="geekblue">{sp.category}</Tag>
                <Text strong>z={sp.z.toFixed(2)}</Text>{' '}
                <Text type="secondary" style={{ fontSize: 11 }}>
                  count {sp.count}
                </Text>
              </div>
              {sp.top_keywords_delta.length > 0 && (
                <Paragraph style={{ margin: '2px 0', fontSize: 11 }}>
                  Δkw:{' '}
                  {sp.top_keywords_delta.slice(0, 4).map((k) => (
                    <Tag key={k.keyword} color="orange" style={{ fontSize: 10 }}>
                      {k.keyword} +{k.delta}
                    </Tag>
                  ))}
                </Paragraph>
              )}
              {sp.matched_events.length > 0 && (
                <Paragraph style={{ margin: '2px 0', fontSize: 11 }}>
                  event:{' '}
                  {sp.matched_events.slice(0, 2).map((e) => (
                    <Tag key={e.title} color="purple" style={{ fontSize: 10 }}>
                      {e.title} (lag {e.lag_days}d)
                    </Tag>
                  ))}
                </Paragraph>
              )}
              {sp.inferred_cause && (
                <Text type="secondary" style={{ fontSize: 11 }}>
                  추정 원인: {sp.inferred_cause}
                </Text>
              )}
            </List.Item>
          )}
        />
      )}
    </Card>
  );
}
