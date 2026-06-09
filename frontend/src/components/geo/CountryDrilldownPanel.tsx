import { Alert, Card, Empty, List, Spin, Statistic, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { fetchCountryDrilldown } from '../../services/geoApi';
import { useFilterStore } from '../../stores/useFilterStore';

const { Text } = Typography;

interface Props {
  code: string | null;
}

function sentTag(sent: number) {
  const color = sent > 0.1 ? 'green' : sent < -0.1 ? 'red' : 'default';
  return <Tag color={color}>{sent.toFixed(2)}</Tag>;
}

export default function CountryDrilldownPanel({ code }: Props) {
  const { dateRange, products } = useFilterStore();

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['country-drilldown', code, dateRange.start, dateRange.end, products.join(',')],
    queryFn: () =>
      fetchCountryDrilldown(code!, {
        start: dateRange.start,
        end: dateRange.end,
        products,
      }),
    enabled: !!code,
    staleTime: 60_000,
  });

  if (!code) {
    return (
      <Card title="국가 상세" bodyStyle={{ padding: 24 }}>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="지도에서 국가를 선택하세요"
        />
      </Card>
    );
  }

  return (
    <Card
      title={
        <span>
          {data?.country_name || code} <Text type="secondary" style={{ fontSize: 12 }}>({code})</Text>
        </span>
      }
      bodyStyle={{ padding: 16 }}
    >
      {isLoading && (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin />
        </div>
      )}
      {isError && (
        <Alert
          type="error"
          showIcon
          message="드릴다운 조회 실패"
          description={error instanceof Error ? error.message : '알 수 없는 오류'}
        />
      )}
      {data && !isLoading && (
        <>
          <div style={{ display: 'flex', gap: 24, marginBottom: 16 }}>
            <Statistic title="총 VoC" value={data.total_count} />
            <Statistic
              title="평균 감성"
              value={data.sent_avg}
              precision={2}
              valueStyle={{
                color: data.sent_avg > 0.1 ? '#3f8600' : data.sent_avg < -0.1 ? '#cf1322' : undefined,
              }}
            />
          </div>

          <Text strong>상위 사이트</Text>
          <List
            size="small"
            dataSource={data.top_sites}
            renderItem={(s) => (
              <List.Item style={{ padding: '4px 0' }}>
                <span>{s.site_name || s.site_code}</span>
                <span>
                  <Text type="secondary">{s.count.toLocaleString()}</Text> {sentTag(s.sent_avg)}
                </span>
              </List.Item>
            )}
          />

          <Text strong style={{ display: 'block', marginTop: 12 }}>상위 제품</Text>
          <List
            size="small"
            dataSource={data.top_products}
            renderItem={(p) => (
              <List.Item style={{ padding: '4px 0' }}>
                <span>{p.product_name || p.product_code}</span>
                <span>
                  <Text type="secondary">{p.count.toLocaleString()}</Text> {sentTag(p.sent_avg)}
                </span>
              </List.Item>
            )}
          />

          <Text strong style={{ display: 'block', marginTop: 12 }}>상위 카테고리</Text>
          <List
            size="small"
            dataSource={data.top_categories}
            renderItem={(c) => (
              <List.Item style={{ padding: '4px 0' }}>
                <span>{c.category}</span>
                <span>
                  <Text type="secondary">{c.count.toLocaleString()}</Text> {sentTag(c.sent_avg)}
                </span>
              </List.Item>
            )}
          />
        </>
      )}
    </Card>
  );
}
