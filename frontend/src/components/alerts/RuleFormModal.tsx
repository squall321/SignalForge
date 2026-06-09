// P4 트랙 D — 룰 생성 모달.
import { useEffect } from 'react';
import { Form, Input, InputNumber, Modal, Select } from 'antd';
import {
  KNOWN_METRIC_PATHS,
  buildRulePayload,
  type RuleFormValues,
} from './alertsUtils';
import { createRule } from '../../services/alertsApi';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

const OPS = ['>', '<', '>=', '<=', '=='];
const SEVERITIES: { value: 'critical' | 'warning' | 'info'; label: string }[] = [
  { value: 'critical', label: 'critical' },
  { value: 'warning', label: 'warning' },
  { value: 'info', label: 'info' },
];

export default function RuleFormModal({ open, onClose, onCreated }: Props) {
  const [form] = Form.useForm<RuleFormValues>();

  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue({
        op: '>=',
        severity: 'warning',
        cooldown_sec: 900,
        threshold: 1,
      });
    }
  }, [open, form]);

  const onOk = async () => {
    const v = (await form.validateFields()) as RuleFormValues & {
      metric_path_custom?: string;
    };
    const merged: RuleFormValues = {
      ...v,
      metric_path: v.metric_path_custom?.trim() || v.metric_path,
    };
    const payload = buildRulePayload(merged);
    await createRule(payload);
    onCreated();
    onClose();
  };

  return (
    <Modal
      title="새 알림 룰"
      open={open}
      onCancel={onClose}
      onOk={onOk}
      okText="생성"
      cancelText="취소"
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="name"
          label="이름"
          rules={[{ required: true, max: 64 }]}
        >
          <Input placeholder="my_alert_rule" />
        </Form.Item>
        <Form.Item
          name="metric_path"
          label="지표 경로"
          rules={[{ required: true, max: 128 }]}
        >
          <Select
            showSearch
            options={KNOWN_METRIC_PATHS}
            placeholder="기존 지표 선택 또는 직접 입력"
            optionFilterProp="label"
          />
        </Form.Item>
        <Form.Item
          name="metric_path_custom"
          label="(선택) 사용자 정의 지표"
          tooltip="입력 시 위 Select 값을 덮어씁니다"
        >
          <Input placeholder="예: community.my_metric" />
        </Form.Item>
        <Form.Item name="op" label="연산자" rules={[{ required: true }]}>
          <Select options={OPS.map((o) => ({ value: o, label: o }))} />
        </Form.Item>
        <Form.Item name="threshold" label="임계값" rules={[{ required: true }]}>
          <InputNumber style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="severity" label="심각도" rules={[{ required: true }]}>
          <Select options={SEVERITIES} />
        </Form.Item>
        <Form.Item
          name="cooldown_sec"
          label="쿨다운 (초)"
          rules={[{ required: true }]}
        >
          <InputNumber min={10} max={86400} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="description" label="설명">
          <Input.TextArea rows={2} maxLength={300} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
