import { DatePicker, Select, Space, Button, Segmented } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useFilterStore } from '../../stores/useFilterStore';
import {
  CATEGORY_CATALOG,
  PLATFORM_CATALOG,
  REGION_CATALOG,
  useProductOptions,
} from '../../hooks/useFilterMeta';

const { RangePicker } = DatePicker;

// P4.3 트랙 B — 빠른 기간 선택 (Segmented) + 카테고리 다중 선택 추가.
// 실 메타: products 는 /api/v1/products 로딩, 나머지는 도메인 카탈로그.
const PERIOD_OPTIONS = [
  { label: '7d', value: 7 },
  { label: '14d', value: 14 },
  { label: '30d', value: 30 },
  { label: '60d', value: 60 },
  { label: '90d', value: 90 },
  { label: 'Custom', value: 0 },
];

const REGION_OPTIONS = REGION_CATALOG.map((r) => ({ label: r.label, value: r.code }));
const PLATFORM_OPTIONS = PLATFORM_CATALOG.map((p) => ({ label: p.label, value: p.code }));
const CATEGORY_OPTIONS = CATEGORY_CATALOG.map((c) => ({ label: c.label, value: c.code }));

export default function GlobalFilterBar() {
  const {
    dateRange,
    products,
    regions,
    platforms,
    categories,
    periodDays,
    setDateRange,
    setProducts,
    setRegions,
    setPlatforms,
    setCategories,
    setPeriodDays,
    reset,
  } = useFilterStore();
  const { data: productMeta } = useProductOptions();

  const productOptions = (productMeta ?? []).map((p) => ({ label: p.name, value: p.code }));

  const rangeValue: [Dayjs | null, Dayjs | null] = [
    dateRange.start ? dayjs(dateRange.start) : null,
    dateRange.end ? dayjs(dateRange.end) : null,
  ];
  const isCustomPeriod = periodDays === 0;

  return (
    <Space size={12} wrap data-testid="global-filter-bar">
      <Segmented
        options={PERIOD_OPTIONS}
        value={periodDays}
        onChange={(v) => setPeriodDays(Number(v))}
        data-testid="period-segmented"
        size="small"
      />
      {isCustomPeriod && (
        <RangePicker
          value={rangeValue}
          onChange={(vals) => {
            if (!vals || !vals[0] || !vals[1]) {
              setDateRange({ start: '', end: '' });
              return;
            }
            setDateRange({
              start: vals[0].format('YYYY-MM-DD'),
              end: vals[1].format('YYYY-MM-DD'),
            });
          }}
          allowClear
          placeholder={['시작일', '종료일']}
          data-testid="custom-range"
        />
      )}
      <Select
        mode="multiple"
        allowClear
        style={{ minWidth: 200 }}
        placeholder="제품"
        value={products}
        onChange={setProducts}
        options={productOptions}
        maxTagCount="responsive"
        data-testid="filter-products"
      />
      <Select
        mode="multiple"
        allowClear
        style={{ minWidth: 160 }}
        placeholder="지역"
        value={regions}
        onChange={setRegions}
        options={REGION_OPTIONS}
        maxTagCount="responsive"
        data-testid="filter-regions"
      />
      <Select
        mode="multiple"
        allowClear
        style={{ minWidth: 180 }}
        placeholder="플랫폼"
        value={platforms}
        onChange={setPlatforms}
        options={PLATFORM_OPTIONS}
        maxTagCount="responsive"
        data-testid="filter-platforms"
      />
      <Select
        mode="multiple"
        allowClear
        style={{ minWidth: 180 }}
        placeholder="카테고리"
        value={categories}
        onChange={setCategories}
        options={CATEGORY_OPTIONS}
        maxTagCount="responsive"
        data-testid="filter-categories"
      />
      <Button onClick={reset} data-testid="filter-reset">초기화</Button>
    </Space>
  );
}
