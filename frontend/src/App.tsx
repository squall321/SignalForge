import { lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { Spin } from 'antd';
import AppLayout from './components/layout/AppLayout';

// UX R2 트랙 C — main bundle 다이어트.
// Dashboard 도 lazy 로 옮긴다. eager 였을 때 KPICard / TodaySignal / QuickActions /
// FavoritesSection / cardRegistry / dashboardApi 가 모두 main 에 끌려와 34.7KB 까지 부풀었다.
// 첫 화면 라우팅 자체는 "/" → "/dashboard" redirect 이므로 Suspense fallback 1 frame 만 추가된다.
const Dashboard = lazy(() => import('./pages/Dashboard'));
const TemporalInsight = lazy(() => import('./pages/TemporalInsight'));
const KnowledgeGraph = lazy(() => import('./pages/KnowledgeGraph'));
const GeoView = lazy(() => import('./pages/GeoView'));
const CommunityView = lazy(() => import('./pages/CommunityView'));
const DeepInsights = lazy(() => import('./pages/DeepInsights'));
const Alerts = lazy(() => import('./pages/Alerts'));
const Compare = lazy(() => import('./pages/Compare'));
const CollectionStatus = lazy(() => import('./pages/CollectionStatus'));
const History = lazy(() => import('./pages/History'));
const DataQuality = lazy(() => import('./pages/DataQuality'));
const ChartGallery = lazy(() => import('./pages/ChartGallery'));

const PageLoader = () => (
  <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
    <Spin tip="페이지 로딩 중..." />
  </div>
);

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route
          path="/dashboard"
          element={
            <Suspense fallback={<PageLoader />}>
              <Dashboard />
            </Suspense>
          }
        />
        <Route
          path="/temporal"
          element={
            <Suspense fallback={<PageLoader />}>
              <TemporalInsight />
            </Suspense>
          }
        />
        <Route
          path="/kg"
          element={
            <Suspense fallback={<PageLoader />}>
              <KnowledgeGraph />
            </Suspense>
          }
        />
        <Route
          path="/geo"
          element={
            <Suspense fallback={<PageLoader />}>
              <GeoView />
            </Suspense>
          }
        />
        <Route
          path="/community"
          element={
            <Suspense fallback={<PageLoader />}>
              <CommunityView />
            </Suspense>
          }
        />
        <Route
          path="/insights"
          element={
            <Suspense fallback={<PageLoader />}>
              <DeepInsights />
            </Suspense>
          }
        />
        <Route
          path="/alerts"
          element={
            <Suspense fallback={<PageLoader />}>
              <Alerts />
            </Suspense>
          }
        />
        <Route
          path="/compare"
          element={
            <Suspense fallback={<PageLoader />}>
              <Compare />
            </Suspense>
          }
        />
        <Route
          path="/collection"
          element={
            <Suspense fallback={<PageLoader />}>
              <CollectionStatus />
            </Suspense>
          }
        />
        <Route
          path="/history"
          element={
            <Suspense fallback={<PageLoader />}>
              <History />
            </Suspense>
          }
        />
        <Route
          path="/data-quality"
          element={
            <Suspense fallback={<PageLoader />}>
              <DataQuality />
            </Suspense>
          }
        />
        <Route
          path="/charts"
          element={
            <Suspense fallback={<PageLoader />}>
              <ChartGallery />
            </Suspense>
          }
        />
        {/* SPA wildcard — 포털 prefix 아래에서 deep-link refresh 시 dashboard 로 안전 복귀 */}
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  );
}
