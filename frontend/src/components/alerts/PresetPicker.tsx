// P4.2 E5 — 알림 룰 프리셋 선택 모달.
// 운영자가 카드 5개에서 다중 선택 → 한 번에 룰 생성.
import { useEffect, useMemo, useState } from 'react';
import { Alert, Card, Checkbox, Modal, Space, Tag, Typography, message } from 'antd';
import {
  applyPresets,
  fetchPresets,
  type AlertPreset,
} from '../../services/alertsApi';
import { togglePresetKey } from './alertsUtils';

const SEVERITY_COLOR: Record<string, string> = {
  critical: 'red',
  warning: 'orange',
  info: 'blue',
};

interface Props {
  open: boolean;
  onClose: () => void;
  onApplied: (created: number, skipped: string[]) => void;
}

export default function PresetPicker({ open, onClose, onApplied }: Props) {
  const [presets, setPresets] = useState<AlertPreset[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSelected(new Set());
    setLoading(true);
    fetchPresets()
      .then(setPresets)
      .catch(() => message.error('프리셋 로드 실패'))
      .finally(() => setLoading(false));
  }, [open]);

  const toggle = (key: string) => {
    setSelected((prev) => togglePresetKey(prev, key));
  };

  const okDisabled = useMemo(() => selected.size === 0, [selected]);

  const onOk = async () => {
    if (okDisabled) return;
    setSubmitting(true);
    try {
      const r = await applyPresets(Array.from(selected));
      message.success(`생성 ${r.created} · 건너뜀 ${r.skipped.length}`);
      onApplied(r.created, r.skipped);
      onClose();
    } catch {
      message.error('프리셋 적용 실패');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="알림 룰 프리셋"
      open={open}
      onCancel={onClose}
      onOk={onOk}
      okText={`선택 룰 적용 (${selected.size})`}
      cancelText="취소"
      okButtonProps={{ disabled: okDisabled, loading: submitting }}
      width={680}
      destroyOnClose
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="중복 이름은 자동 skip — 한 번 적용 후 다시 클릭해도 안전."
        />
        {loading ? (
          <Typography.Text type="secondary">로딩 중…</Typography.Text>
        ) : (
          presets.map((p) => {
            const checked = selected.has(p.key);
            return (
              <Card
                key={p.key}
                size="small"
                hoverable
                onClick={() => toggle(p.key)}
                style={{
                  borderColor: checked ? '#1677ff' : undefined,
                  background: checked ? '#f0f7ff' : undefined,
                  cursor: 'pointer',
                }}
                bodyStyle={{ padding: 12 }}
              >
                <Space
                  direction="vertical"
                  size={4}
                  style={{ width: '100%' }}
                >
                  <Space
                    style={{ width: '100%', justifyContent: 'space-between' }}
                  >
                    <Space>
                      <Checkbox
                        checked={checked}
                        onChange={() => toggle(p.key)}
                        onClick={(e) => e.stopPropagation()}
                      />
                      <Typography.Text strong>{p.name}</Typography.Text>
                      <Tag color={SEVERITY_COLOR[p.severity] ?? 'default'}>
                        {p.severity}
                      </Tag>
                    </Space>
                    <Typography.Text code style={{ fontSize: 12 }}>
                      {p.op} {p.threshold}
                    </Typography.Text>
                  </Space>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {p.metric_path} · cooldown {p.cooldown_sec}s
                  </Typography.Text>
                  {p.description && (
                    <Typography.Text style={{ fontSize: 12 }}>
                      {p.description}
                    </Typography.Text>
                  )}
                </Space>
              </Card>
            );
          })
        )}
      </Space>
    </Modal>
  );
}
