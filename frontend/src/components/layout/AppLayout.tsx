import { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import { Layout, Menu, theme, Drawer, Button, Grid, Tooltip, message } from 'antd';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  DashboardOutlined,
  LineChartOutlined,
  AlertOutlined,
  BulbOutlined,
  ShareAltOutlined,
  GlobalOutlined,
  TeamOutlined,
  MenuOutlined,
  SearchOutlined,
  QuestionCircleOutlined,
  SwapOutlined,
  DatabaseOutlined,
  HistoryOutlined,
  SafetyCertificateOutlined,
  BarChartOutlined,
} from '@ant-design/icons';
import GlobalFilterBar from './GlobalFilterBar';
import LiveAlertBadge from '../dashboard/LiveAlertBadge';
import { useFilterUrlSync } from '../../hooks/useFilterUrlSync';
import { useShortcuts } from '../../hooks/useShortcuts';
import { isMobileBreakpoint, contentPadding, headerPadding } from './responsive';

// CommandPalette / ShortcutsHelp 는 modal — 첫 진입 시점에 보일 가능성이 낮으므로
// lazy 로 분리해 main 청크에서 제외.
const CommandPalette = lazy(() => import('../global/CommandPalette'));
const ShortcutsHelp = lazy(() => import('../global/ShortcutsHelp'));

const { Header, Sider, Content } = Layout;
const { useBreakpoint } = Grid;

const MENU = [
  { key: '/dashboard', icon: <DashboardOutlined />, label: 'Overview' },
  { key: '/temporal', icon: <LineChartOutlined />, label: '시계열 인사이트' },
  { key: '/kg', icon: <ShareAltOutlined />, label: '지식 그래프' },
  { key: '/geo', icon: <GlobalOutlined />, label: '국가 분석' },
  { key: '/community', icon: <TeamOutlined />, label: '커뮤니티' },
  { key: '/insights', icon: <BulbOutlined />, label: '딥 인사이트' },
  { key: '/alerts', icon: <AlertOutlined />, label: '실시간 알림' },
  { key: '/compare', icon: <SwapOutlined />, label: '비교' },
  { key: '/collection', icon: <DatabaseOutlined />, label: '수집 상태' },
  { key: '/history', icon: <HistoryOutlined />, label: '17년 라이프사이클' },
  { key: '/data-quality', icon: <SafetyCertificateOutlined />, label: '데이터 품질' },
  { key: '/charts', icon: <BarChartOutlined />, label: '차트 갤러리' },
];

// 모바일(xs/sm)에서는 Sider를 숨기고 햄버거 → Drawer 로 전환.
// AntD breakpoints: xs <576, sm <768, md >=768.
// 여기서는 md 미만(=screens.md === false) 을 "모바일" 로 정의한다.
function Brand() {
  return (
    <div
      data-testid="brand"
      style={{
        height: 56,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontWeight: 700,
        fontSize: 18,
        color: '#1677ff',
      }}
    >
      SignalForge
    </div>
  );
}

export default function AppLayout() {
  // URL <-> store 양방향 동기화는 layout 마운트 시점에 한 번 활성화
  useFilterUrlSync();
  const navigate = useNavigate();
  const location = useLocation();
  const screens = useBreakpoint();
  const isMobile = isMobileBreakpoint(screens);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const {
    token: { colorBgContainer, colorBorderSecondary },
  } = theme.useToken();

  // 라우트 변경 시 drawer 자동 닫힘
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  // 트랙 D — 전역 단축키 hook
  const openSearch = useCallback(() => setPaletteOpen(true), []);
  const openHelp = useCallback(() => setHelpOpen(true), []);
  const focusFilter = useCallback(() => {
    const el = document.querySelector<HTMLInputElement>(
      '.ant-picker-input input, .ant-select-selection-search-input',
    );
    if (el) {
      el.focus();
      message.success({ content: '필터바 포커스', duration: 1, key: 'focus-filter' });
    }
  }, []);
  useShortcuts({ openSearch, openHelp, focusFilter });

  const menu = (
    <Menu
      mode="inline"
      selectedKeys={[location.pathname]}
      items={MENU}
      onClick={(e) => navigate(e.key)}
      style={{ borderRight: 0 }}
    />
  );

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {!isMobile && (
        <Sider
          theme="light"
          width={220}
          style={{ borderRight: `1px solid ${colorBorderSecondary}` }}
          data-testid="sider"
        >
          <div style={{ borderBottom: `1px solid ${colorBorderSecondary}` }}>
            <Brand />
          </div>
          {menu}
        </Sider>
      )}
      <Layout>
        <Header
          style={{
            background: colorBgContainer,
            padding: headerPadding(isMobile),
            borderBottom: `1px solid ${colorBorderSecondary}`,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            overflowX: 'auto',
          }}
        >
          {isMobile && (
            <Button
              type="text"
              icon={<MenuOutlined />}
              onClick={() => setDrawerOpen(true)}
              aria-label="open-menu"
              data-testid="menu-toggle"
            />
          )}
          <Tooltip title="검색 (Ctrl/Cmd+K)">
            <Button
              icon={<SearchOutlined />}
              onClick={openSearch}
              data-testid="search-trigger"
              aria-label="open-search"
            >
              {!isMobile && '검색'}
            </Button>
          </Tooltip>
          <div style={{ flex: 1, minWidth: 0 }}>
            <GlobalFilterBar />
          </div>
          <Tooltip title="단축키 도움말 (?)">
            <Button
              type="text"
              icon={<QuestionCircleOutlined />}
              onClick={openHelp}
              data-testid="help-trigger"
              aria-label="open-help"
            />
          </Tooltip>
          <LiveAlertBadge />
        </Header>
        <Content
          style={{
            padding: contentPadding(isMobile),
            background: '#f5f7fa',
            overflowX: 'auto',
          }}
        >
          <Outlet />
        </Content>
      </Layout>

      <Drawer
        placement="left"
        open={isMobile && drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={240}
        styles={{ body: { padding: 0 } }}
        title={<Brand />}
        data-testid="menu-drawer"
      >
        {menu}
      </Drawer>

      <Suspense fallback={null}>
        {paletteOpen && (
          <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
        )}
        {helpOpen && (
          <ShortcutsHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
        )}
      </Suspense>
    </Layout>
  );
}
