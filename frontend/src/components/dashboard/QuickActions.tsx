// P5 R6 트랙 A — 빠른 진입 버튼 4종.
import { Card, Button, Row, Col } from 'antd';
import {
  LineChartOutlined,
  ShareAltOutlined,
  BulbOutlined,
  AlertOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';

interface ActionItem {
  key: string;
  label: string;
  path: string;
  icon: React.ReactNode;
  color: string;
}

const ACTIONS: ActionItem[] = [
  { key: 'temporal', label: '시계열 보기', path: '/temporal', icon: <LineChartOutlined />, color: '#1677ff' },
  { key: 'kg',       label: '지식 그래프', path: '/kg',       icon: <ShareAltOutlined />, color: '#722ed1' },
  { key: 'insights', label: '딥 인사이트', path: '/insights', icon: <BulbOutlined />,     color: '#52c41a' },
  { key: 'alerts',   label: '알림 관리',   path: '/alerts',   icon: <AlertOutlined />,    color: '#fa541c' },
];

export default function QuickActions() {
  const navigate = useNavigate();
  return (
    <Card size="small" title="빠른 진입">
      <Row gutter={[8, 8]}>
        {ACTIONS.map((a) => (
          <Col xs={12} sm={6} key={a.key}>
            <Button
              block
              size="large"
              icon={<span style={{ color: a.color }}>{a.icon}</span>}
              onClick={() => navigate(a.path)}
              data-testid={`quick-${a.key}`}
            >
              {a.label}
            </Button>
          </Col>
        ))}
      </Row>
    </Card>
  );
}
