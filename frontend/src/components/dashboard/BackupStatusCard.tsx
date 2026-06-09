// Track E — Drive 백업 안전 상태 카드.
// /api/v1/_internal/backup-status 응답을 mini KPI 처럼 표시.
// 운영자가 대시보드 첫 화면에서 "어제 백업이 안전한가" 5초 안에 확인.
import { useEffect, useState } from 'react';
import { Card, Tag, Tooltip, Typography } from 'antd';
import {
  CheckCircleTwoTone,
  CloseCircleTwoTone,
  CloudUploadOutlined,
  QuestionCircleTwoTone,
} from '@ant-design/icons';
import api from '../../services/api';

const { Text } = Typography;

export interface BackupStatus {
  available: boolean;
  ok: boolean | null;
  reason?: string;
  drive_path?: string;
  file?: string;
  size_bytes?: number;
  mtime?: string;
  age_hours?: number;
  max_age_hours?: number;
  sha256?: string;
  error?: string;
  path?: string;
}

function fmtMB(n?: number): string {
  if (!n || n <= 0) return '—';
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function reasonLabel(reason?: string): string {
  switch (reason) {
    case 'ok':
      return '정상';
    case 'stale':
      return '오래됨';
    case 'size_too_small':
      return '크기 미달';
    case 'sha256_missing_or_invalid':
      return 'sha256 결함';
    case 'no_backup_file':
      return '백업 없음';
    default:
      return reason || '알 수 없음';
  }
}

export default function BackupStatusCard() {
  const [data, setData] = useState<BackupStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api
      .get<BackupStatus>('/_internal/backup-status')
      .then((res) => {
        if (alive) setData(res.data);
      })
      .catch(() => {
        if (alive) setData(null);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  // 상태별 아이콘·색·라벨.
  const icon = (() => {
    if (loading || !data) return <QuestionCircleTwoTone twoToneColor="#8c8c8c" />;
    if (!data.available || data.ok === null) {
      return <QuestionCircleTwoTone twoToneColor="#8c8c8c" />;
    }
    return data.ok ? (
      <CheckCircleTwoTone twoToneColor="#52c41a" />
    ) : (
      <CloseCircleTwoTone twoToneColor="#cf1322" />
    );
  })();

  const headlineColor = (() => {
    if (loading || !data || !data.available) return '#8c8c8c';
    return data.ok ? '#237804' : '#cf1322';
  })();

  const headline = (() => {
    if (loading) return '확인 중…';
    if (!data || !data.available) return '검증 기록 없음';
    return data.ok ? '백업 안전' : '백업 확인 필요';
  })();

  const subline = (() => {
    if (!data || !data.available) return '아직 verify-backup.sh 가 실행되지 않았습니다.';
    const parts: string[] = [];
    if (data.file) parts.push(data.file);
    if (data.age_hours !== undefined) parts.push(`${data.age_hours}h 전`);
    if (data.size_bytes) parts.push(fmtMB(data.size_bytes));
    return parts.join(' · ') || '—';
  })();

  const tooltipText = data?.available
    ? [
        `drive_path: ${data.drive_path ?? '—'}`,
        `mtime: ${data.mtime ?? '—'}`,
        `max_age_hours: ${data.max_age_hours ?? '—'}`,
        `sha256: ${data.sha256?.slice(0, 12) ?? '—'}…`,
      ].join('\n')
    : '검증 상태 파일이 아직 없습니다.';

  return (
    <Card size="small" bodyStyle={{ padding: '14px 18px' }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <CloudUploadOutlined style={{ color: '#1677ff' }} />
        <Text style={{ fontSize: 13, color: '#595959' }}>Drive 백업 상태</Text>
      </span>
      <Tooltip title={<pre style={{ margin: 0, color: '#fff' }}>{tooltipText}</pre>}>
        <div
          style={{
            marginTop: 6,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 18,
            fontWeight: 600,
            color: headlineColor,
          }}
        >
          {icon}
          <span>{headline}</span>
          {data?.available && (
            <Tag color={data.ok ? 'green' : 'red'} style={{ marginLeft: 'auto' }}>
              {reasonLabel(data.reason)}
            </Tag>
          )}
        </div>
      </Tooltip>
      <div style={{ marginTop: 6, fontSize: 12, color: '#8c8c8c' }}>{subline}</div>
    </Card>
  );
}
