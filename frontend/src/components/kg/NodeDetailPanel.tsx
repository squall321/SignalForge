// 노드 클릭 시 노출되는 사이드 패널 (샘플 5건) — P2-3 T1
import { Drawer, List, Card, Tag, Typography, Empty, Spin, Button, Space } from 'antd';
import { LinkOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { fetchKGNodeSamples } from '../../services/kgApi';
import { NODE_TYPE_COLOR, type KGNodeType } from '../../types/kg';

const { Text, Paragraph } = Typography;

export interface NodeDetailPanelProps {
  open: boolean;
  nodeId: string | null;
  nodeLabel?: string;
  nodeType?: KGNodeType;
  onClose: () => void;
}

function sentimentTag(score: number | null) {
  if (score == null) return <Tag>중립?</Tag>;
  if (score >= 0.1) return <Tag color="green">긍정 {score.toFixed(2)}</Tag>;
  if (score <= -0.1) return <Tag color="red">부정 {score.toFixed(2)}</Tag>;
  return <Tag>중립 {score.toFixed(2)}</Tag>;
}

export default function NodeDetailPanel({
  open,
  nodeId,
  nodeLabel,
  nodeType,
  onClose,
}: NodeDetailPanelProps) {
  const { data, isFetching, isError } = useQuery({
    queryKey: ['kg', 'node', nodeId, 'samples'],
    queryFn: () => fetchKGNodeSamples(nodeId!, 5),
    enabled: open && !!nodeId,
    staleTime: 30_000,
    retry: 0,
  });

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={420}
      title={
        <Space>
          {nodeType && (
            <Tag color={NODE_TYPE_COLOR[nodeType]} style={{ marginRight: 0 }}>
              {nodeType}
            </Tag>
          )}
          <Text strong>{nodeLabel || nodeId}</Text>
        </Space>
      }
      destroyOnClose
    >
      {isFetching && (
        <div style={{ textAlign: 'center', padding: '32px 0' }}>
          <Spin />
        </div>
      )}
      {!isFetching && isError && (
        <Empty description="샘플을 불러올 수 없습니다 (API 미연결 또는 데이터 없음)" />
      )}
      {!isFetching && !isError && (!data || data.samples.length === 0) && (
        <Empty description="이 노드와 연결된 VOC 샘플이 없습니다" />
      )}
      {!isFetching && !isError && data && data.samples.length > 0 && (
        <List
          dataSource={data.samples}
          renderItem={(s) => (
            <List.Item key={s.voc_id} style={{ padding: '8px 0' }}>
              <Card size="small" style={{ width: '100%' }}>
                <Space size={6} wrap style={{ marginBottom: 6 }}>
                  {sentimentTag(s.sentiment_score)}
                  {s.platform_code && <Tag>{s.platform_code}</Tag>}
                  {s.country_code && <Tag>{s.country_code}</Tag>}
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {new Date(s.collected_at).toLocaleDateString()}
                  </Text>
                </Space>
                {s.title && (
                  <Paragraph strong style={{ marginBottom: 4 }}>
                    {s.title}
                  </Paragraph>
                )}
                <Paragraph
                  style={{ marginBottom: 8, fontSize: 13 }}
                  ellipsis={{ rows: 3, expandable: true, symbol: '더보기' }}
                >
                  {s.excerpt}
                </Paragraph>
                {s.url && (
                  <Button
                    type="link"
                    size="small"
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    icon={<LinkOutlined />}
                    style={{ padding: 0 }}
                  >
                    원문 열기
                  </Button>
                )}
              </Card>
            </List.Item>
          )}
        />
      )}
    </Drawer>
  );
}
