// P4 트랙 D — Slack / WebSocket 채널 상태.
import { Card, Descriptions, Tag } from 'antd';
import type { ChannelStatus } from '../../services/alertsApi';

interface Props {
  status: ChannelStatus | null;
  loading?: boolean;
}

export default function ChannelStatusPanel({ status, loading }: Props) {
  return (
    <Card title="채널 상태" size="small" loading={loading}>
      <Descriptions column={1} size="small">
        <Descriptions.Item label="Slack">
          {status?.slack.enabled ? (
            <Tag color="green">실 전송</Tag>
          ) : (
            <Tag color="default">dry-run (Webhook 미설정)</Tag>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="WebSocket 연결">
          <Tag color={status && status.websocket.connections > 0 ? 'blue' : 'default'}>
            {status?.websocket.connections ?? 0}
          </Tag>
        </Descriptions.Item>
      </Descriptions>
    </Card>
  );
}
