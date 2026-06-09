// P4.2 R6 트랙 D — 단축키 도움말 모달.
import { Modal, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';

export interface ShortcutRow {
  keys: string[];
  desc: string;
  category: '탐색' | '필터' | '검색' | '도움말';
}

const ROWS: ShortcutRow[] = [
  { keys: ['Ctrl/Cmd', 'K'], desc: '검색 (Command Palette)', category: '검색' },
  { keys: ['/'], desc: '검색 (단일키)', category: '검색' },
  { keys: ['?'], desc: '단축키 도움말', category: '도움말' },
  { keys: ['g', 'd'], desc: 'Overview (대시보드)', category: '탐색' },
  { keys: ['g', 't'], desc: '시계열 인사이트', category: '탐색' },
  { keys: ['g', 'k'], desc: '지식 그래프', category: '탐색' },
  { keys: ['g', 'g'], desc: '국가 분석', category: '탐색' },
  { keys: ['g', 'c'], desc: '커뮤니티', category: '탐색' },
  { keys: ['g', 'i'], desc: '딥 인사이트', category: '탐색' },
  { keys: ['g', 'a'], desc: '실시간 알림', category: '탐색' },
  { keys: ['f'], desc: '필터바 포커스', category: '필터' },
];

const columns: ColumnsType<ShortcutRow> = [
  {
    title: '카테고리',
    dataIndex: 'category',
    key: 'category',
    width: 90,
    render: (v: ShortcutRow['category']) => <Tag color={catColor(v)}>{v}</Tag>,
  },
  {
    title: '단축키',
    dataIndex: 'keys',
    key: 'keys',
    width: 180,
    render: (keys: string[]) => (
      <span>
        {keys.map((k, i) => (
          <span key={i}>
            <kbd
              style={{
                background: '#f0f0f0',
                border: '1px solid #d9d9d9',
                borderRadius: 3,
                padding: '1px 6px',
                fontSize: 12,
                fontFamily: 'monospace',
              }}
            >
              {k}
            </kbd>
            {i < keys.length - 1 && <span style={{ margin: '0 4px', color: '#999' }}>+</span>}
          </span>
        ))}
      </span>
    ),
  },
  { title: '설명', dataIndex: 'desc', key: 'desc' },
];

function catColor(c: ShortcutRow['category']): string {
  switch (c) {
    case '탐색': return 'blue';
    case '필터': return 'purple';
    case '검색': return 'green';
    case '도움말': return 'gold';
  }
}

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function ShortcutsHelp({ open, onClose }: Props) {
  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      title="키보드 단축키"
      width={560}
      data-testid="shortcuts-help-modal"
    >
      <Table
        size="small"
        rowKey={(r) => r.keys.join('+')}
        columns={columns}
        dataSource={ROWS}
        pagination={false}
      />
    </Modal>
  );
}

export { ROWS as SHORTCUT_ROWS };
