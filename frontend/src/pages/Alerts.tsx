import { useEffect, useState } from 'react';
import {
  Alert, Badge, Button, Card, Col, Popconfirm, Row, Space, Switch, Table, Tag, Typography, message,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, ThunderboltOutlined, DeleteOutlined, AppstoreAddOutlined,
} from '@ant-design/icons';
import {
  deleteRule,
  fetchAlertMonitor,
  fetchChannels,
  fetchRecent,
  fetchRules,
  fireTest,
  openAlertSocket,
  patchRule,
  type AlertEvent,
  type AlertMonitorResponse,
  type AlertRule,
  type ChannelStatus,
} from '../services/alertsApi';
import RuleFormModal from '../components/alerts/RuleFormModal';
import PresetPicker from '../components/alerts/PresetPicker';
import AlertTimeline from '../components/alerts/AlertTimeline';
import ChannelStatusPanel from '../components/alerts/ChannelStatusPanel';
import AlertMonitorPanel from '../components/alerts/AlertMonitorPanel';

const SEVERITY_COLOR: Record<string, string> = {
  critical: 'red',
  warning: 'orange',
  info: 'blue',
};

export default function AlertsPage() {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [channels, setChannels] = useState<ChannelStatus | null>(null);
  const [live, setLive] = useState<AlertEvent[]>([]);
  const [monitor, setMonitor] = useState<AlertMonitorResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [firing, setFiring] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [presetOpen, setPresetOpen] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      const [r, e, ch, mon] = await Promise.all([
        fetchRules(),
        fetchRecent(200),
        fetchChannels(),
        // /_internal endpoint — 외부 nginx 차단 시 실패해도 다른 패널은 살린다.
        fetchAlertMonitor(7).catch(() => null),
      ]);
      setRules(r);
      setEvents(e);
      setChannels(ch);
      setMonitor(mon);
    } catch {
      message.error('로드 실패');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    const ws = openAlertSocket((msg) => {
      if (msg.type === 'alert') {
        setLive((prev) => [msg.data as AlertEvent, ...prev].slice(0, 20));
      }
    });
    return () => ws.close();
  }, []);

  const handleFire = async () => {
    setFiring(true);
    try {
      const r = await fireTest();
      message.success(`평가 ${r.evaluated}룰 · 발화 ${r.fired}`);
      await refresh();
    } catch {
      message.error('테스트 발화 실패');
    } finally {
      setFiring(false);
    }
  };

  const handleToggle = async (rule: AlertRule, next: boolean) => {
    // 낙관 갱신
    setRules((prev) => prev.map((r) => (r.id === rule.id ? { ...r, is_active: next } : r)));
    try {
      await patchRule(rule.id, { is_active: next });
      message.success(`${rule.name} ${next ? '활성' : '비활성'}`);
    } catch {
      // 롤백
      setRules((prev) => prev.map((r) => (r.id === rule.id ? { ...r, is_active: !next } : r)));
      message.error('토글 실패');
    }
  };

  const handleDelete = async (rule: AlertRule) => {
    try {
      await deleteRule(rule.id);
      setRules((prev) => prev.filter((r) => r.id !== rule.id));
      message.success(`${rule.name} 삭제`);
    } catch {
      message.error('삭제 실패');
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Typography.Title level={3}>실시간 알림 (P4)</Typography.Title>

      <Alert
        type="info"
        showIcon
        message="Slack · WebSocket 채널 동작 — Webhook URL 미설정 시 dry-run 로그"
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} md={16}>
          <Card
            title={
              <Space>
                활성 룰{' '}
                <Badge
                  count={rules.filter((r) => r.is_active).length}
                  showZero
                  color="green"
                />
              </Space>
            }
            extra={
              <Space>
                <Button
                  icon={<PlusOutlined />}
                  type="primary"
                  size="small"
                  onClick={() => setModalOpen(true)}
                >
                  새 룰
                </Button>
                <Button
                  icon={<AppstoreAddOutlined />}
                  size="small"
                  onClick={() => setPresetOpen(true)}
                >
                  프리셋 추가
                </Button>
                <Button
                  icon={<ReloadOutlined />}
                  onClick={refresh}
                  loading={loading}
                  size="small"
                >
                  새로고침
                </Button>
              </Space>
            }
          >
            <Table<AlertRule>
              dataSource={rules}
              rowKey="id"
              size="small"
              pagination={false}
              columns={[
                { title: '이름', dataIndex: 'name', key: 'name', width: 140 },
                { title: '지표', dataIndex: 'metric_path', key: 'metric', ellipsis: true },
                {
                  title: '조건', key: 'cond', width: 80,
                  render: (_, r) => `${r.op} ${r.threshold}`,
                },
                {
                  title: '심각도', dataIndex: 'severity', key: 'sev', width: 80,
                  render: (s: string) => <Tag color={SEVERITY_COLOR[s] || 'default'}>{s}</Tag>,
                },
                {
                  title: '활성', dataIndex: 'is_active', key: 'act', width: 70,
                  render: (_, r) => (
                    <Switch
                      size="small"
                      checked={r.is_active}
                      onChange={(v) => handleToggle(r, v)}
                    />
                  ),
                },
                {
                  title: '', key: 'del', width: 40,
                  render: (_, r) => (
                    <Popconfirm
                      title={`${r.name} 삭제?`}
                      onConfirm={() => handleDelete(r)}
                      okText="삭제"
                      cancelText="취소"
                    >
                      <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  ),
                },
              ]}
            />
          </Card>
        </Col>

        <Col xs={24} md={8}>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <ChannelStatusPanel status={channels} loading={loading} />
            <Card
              title={<Space>라이브 (WS) <Badge count={live.length} color="red" /></Space>}
              size="small"
              extra={
                <Button
                  icon={<ThunderboltOutlined />}
                  type="primary"
                  onClick={handleFire}
                  loading={firing}
                  size="small"
                >
                  지금 평가
                </Button>
              }
            >
              {live.length === 0 ? (
                <Typography.Text type="secondary">대기 중…</Typography.Text>
              ) : (
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  {live.slice(0, 5).map((ev, i) => (
                    <Alert
                      key={i}
                      type={
                        ev.severity === 'critical' ? 'error' :
                        ev.severity === 'warning' ? 'warning' : 'info'
                      }
                      showIcon
                      message={`${ev.rule_name} (${ev.value} / ${ev.threshold})`}
                    />
                  ))}
                </Space>
              )}
            </Card>
          </Space>
        </Col>
      </Row>

      <AlertMonitorPanel data={monitor} loading={loading} />

      <Card title="발화 timeline (지난 7일, 시간대별)">
        <AlertTimeline events={events} />
      </Card>

      <Card title="최근 발화">
        <Table<AlertEvent>
          dataSource={events.slice(0, 50)}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 10 }}
          columns={[
            {
              title: '발화 시각', dataIndex: 'fired_at', key: 't', width: 200,
              render: (v: string) => (v ? new Date(v).toLocaleString('ko-KR') : ''),
            },
            { title: '룰', dataIndex: 'rule_name', key: 'r', width: 160 },
            {
              title: '심각도', dataIndex: 'severity', key: 's', width: 90,
              render: (s: string) => <Tag color={SEVERITY_COLOR[s] || 'default'}>{s}</Tag>,
            },
            { title: '값', dataIndex: 'value', key: 'v', width: 80 },
            { title: '임계', dataIndex: 'threshold', key: 'th', width: 80 },
            {
              title: '채널', dataIndex: 'dispatched_channels', key: 'ch',
              render: (chs: string[]) => chs?.map((c) => <Tag key={c}>{c}</Tag>),
            },
          ]}
        />
      </Card>

      <RuleFormModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={() => {
          message.success('룰 생성 완료');
          refresh();
        }}
      />

      <PresetPicker
        open={presetOpen}
        onClose={() => setPresetOpen(false)}
        onApplied={() => refresh()}
      />
    </Space>
  );
}
