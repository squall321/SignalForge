// P5 R6 트랙 A — Dashboard API 래퍼.
// /api/v1/dashboard/overview 단일 호출.
import api from './api';
import type { DashboardOverviewResponse } from '../types/dashboard';

export interface FetchOverviewParams {
  period?: '7d' | '30d' | '90d';
  product?: string;
  country?: string;
  platform?: string;
}

export async function fetchOverview(
  params: FetchOverviewParams = {},
): Promise<DashboardOverviewResponse> {
  const { data } = await api.get<DashboardOverviewResponse>(
    '/dashboard/overview',
    { params: { period: '7d', ...params } },
  );
  return data;
}
