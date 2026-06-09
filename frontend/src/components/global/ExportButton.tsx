// R10 트랙 E — 데이터 내보내기 버튼.
//
// 카드 ⋮ 메뉴 또는 페이지 헤더에서 호출. CSV / Excel / PDF 다운로드를
// _internal export endpoint 로 요청한다.
//
// 사용:
//   <ExportButton series="GS25" periodDays={30} />
//   buildCardMenuItems({hasExport: true, ...}) — 카드 메뉴 통합 시
//
// blob 응답을 a[href=blob:] 으로 즉시 다운로드 트리거.
// 실패 시 antd message.error.
import { Dropdown, Button, message } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import api from '../../services/api';

export type ExportFormat = 'csv' | 'excel' | 'pdf';

interface Props {
  series: string;
  periodDays?: number;
  sections?: Array<'kpi' | 'timeline' | 'categories' | 'keywords'>;
  size?: 'small' | 'middle' | 'large';
  buttonText?: string;
}

// 순수 함수 — 단위 테스트 대상 (DOM 의존 없음).
export function buildExportFilename(
  series: string,
  format: ExportFormat,
): string {
  const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD UTC
  const ext = format === 'excel' ? 'xlsx' : format;
  return `voc_${series}_${today}.${ext}`;
}

// 순수 함수 — Content-Disposition 헤더에서 filename 파싱 (없으면 fallback).
export function parseFilenameHeader(
  disposition: string | undefined,
  fallback: string,
): string {
  if (!disposition) return fallback;
  const m = /filename="?([^"]+)"?/i.exec(disposition);
  return m && m[1] ? m[1] : fallback;
}

// 순수 함수 — 메뉴 아이템 정의 (테스트용으로 분리).
export function buildExportMenuItems(): NonNullable<MenuProps['items']> {
  return [
    { key: 'csv', icon: <DownloadOutlined />, label: 'CSV 다운로드' },
    { key: 'excel', icon: <DownloadOutlined />, label: 'Excel 다운로드' },
    { key: 'pdf', icon: <DownloadOutlined />, label: 'PDF 생성' },
  ];
}

async function _download(blob: Blob, filename: string): Promise<void> {
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } finally {
    // 다음 tick 에 revoke — Safari 호환
    setTimeout(() => URL.revokeObjectURL(url), 100);
  }
}

export async function triggerExport(
  format: ExportFormat,
  opts: { series: string; periodDays: number; sections?: string[] },
): Promise<{ ok: boolean; filename: string }> {
  const fallbackName = buildExportFilename(opts.series, format);
  try {
    let resp;
    if (format === 'pdf') {
      resp = await api.post(
        '/_internal/export-pdf',
        {
          product: opts.series,
          period_days: opts.periodDays,
          sections: opts.sections ?? ['kpi', 'timeline', 'categories', 'keywords'],
        },
        { responseType: 'blob' },
      );
    } else {
      resp = await api.get('/_internal/export', {
        params: { type: format, series: opts.series, period_days: opts.periodDays },
        responseType: 'blob',
      });
    }
    const filename = parseFilenameHeader(
      resp.headers?.['content-disposition'] as string | undefined,
      fallbackName,
    );
    await _download(resp.data as Blob, filename);
    return { ok: true, filename };
  } catch (e) {
    console.error('[ExportButton] failed', e);
    return { ok: false, filename: fallbackName };
  }
}

export default function ExportButton({
  series,
  periodDays = 30,
  sections,
  size = 'small',
  buttonText = '내보내기',
}: Props) {
  const items = buildExportMenuItems();

  const onClick: MenuProps['onClick'] = async ({ key }) => {
    const fmt = key as ExportFormat;
    void message.loading({
      content: `${fmt.toUpperCase()} 생성 중...`,
      key: 'export',
      duration: 0,
    });
    const r = await triggerExport(fmt, { series, periodDays, sections });
    if (r.ok) {
      message.success({ content: `${r.filename} 다운로드`, key: 'export' });
    } else {
      message.error({ content: '내보내기 실패', key: 'export' });
    }
  };

  return (
    <Dropdown menu={{ items, onClick }} trigger={['click']}>
      <Button
        type="text"
        size={size}
        icon={<DownloadOutlined />}
        aria-label="export-button"
        data-testid="export-button"
      >
        {buttonText}
      </Button>
    </Dropdown>
  );
}
