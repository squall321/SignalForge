import { useMemo, useState } from 'react';
import { ComposableMap, Geographies, Geography, ZoomableGroup } from 'react-simple-maps';
import { Tooltip } from 'antd';
import type { ChoroplethMode, CountryMetric } from '../../types/geo';
import { buildColorScale, indexByCountry } from './colorScale';

// world-atlas TopoJSON (110m, ~100KB) — 외부 CDN
// 필요 시 public/ 폴더에 정적 파일로 이동 가능.
const TOPO_URL =
  'https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json';

interface Props {
  countries: CountryMetric[];
  mode: ChoroplethMode;
  selectedCode?: string;
  onSelect?: (code: string) => void;
  height?: number;
  // 외부에서 frame override (DiffusionPlayer 가 주입)
  valueOverride?: Record<string, number>;
}

// world-atlas는 ISO numeric code(id)를 사용. ISO numeric(3자리) → ISO alpha-3 매핑.
// 110m TopoJSON 의 주요 국가만 포함 (필요 시 확장).
const NUMERIC_TO_ALPHA3: Record<string, string> = {
  '004': 'AFG', '008': 'ALB', '012': 'DZA', '024': 'AGO', '032': 'ARG',
  '036': 'AUS', '040': 'AUT', '050': 'BGD', '056': 'BEL', '076': 'BRA',
  '100': 'BGR', '124': 'CAN', '152': 'CHL', '156': 'CHN', '170': 'COL',
  '188': 'CRI', '191': 'HRV', '192': 'CUB', '203': 'CZE', '208': 'DNK',
  '218': 'ECU', '222': 'SLV', '231': 'ETH', '246': 'FIN', '250': 'FRA',
  '276': 'DEU', '288': 'GHA', '300': 'GRC', '320': 'GTM', '344': 'HKG',
  '348': 'HUN', '352': 'ISL', '356': 'IND', '360': 'IDN', '364': 'IRN',
  '368': 'IRQ', '372': 'IRL', '376': 'ISR', '380': 'ITA', '388': 'JAM',
  '392': 'JPN', '398': 'KAZ', '400': 'JOR', '404': 'KEN', '408': 'PRK',
  '410': 'KOR', '414': 'KWT', '418': 'LAO', '422': 'LBN', '434': 'LBY',
  '440': 'LTU', '458': 'MYS', '484': 'MEX', '496': 'MNG', '504': 'MAR',
  '508': 'MOZ', '516': 'NAM', '524': 'NPL', '528': 'NLD', '554': 'NZL',
  '566': 'NGA', '578': 'NOR', '586': 'PAK', '604': 'PER', '608': 'PHL',
  '616': 'POL', '620': 'PRT', '634': 'QAT', '642': 'ROU', '643': 'RUS',
  '682': 'SAU', '686': 'SEN', '688': 'SRB', '702': 'SGP', '703': 'SVK',
  '704': 'VNM', '705': 'SVN', '710': 'ZAF', '716': 'ZWE', '724': 'ESP',
  '752': 'SWE', '756': 'CHE', '760': 'SYR', '764': 'THA', '784': 'ARE',
  '788': 'TUN', '792': 'TUR', '804': 'UKR', '818': 'EGY', '826': 'GBR',
  '834': 'TZA', '840': 'USA', '858': 'URY', '860': 'UZB', '862': 'VEN',
  '887': 'YEM',
};

function alpha3FromGeoId(id: string | number): string {
  const s = String(id).padStart(3, '0');
  return NUMERIC_TO_ALPHA3[s] || s;
}

export default function WorldChoropleth({
  countries,
  mode,
  selectedCode,
  onSelect,
  height = 480,
  valueOverride,
}: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const scale = useMemo(() => buildColorScale(countries, mode), [countries, mode]);
  const indexed = useMemo(() => indexByCountry(countries), [countries]);

  return (
    <div style={{ width: '100%', height, background: '#f8fafc', borderRadius: 4 }}>
      <ComposableMap
        projectionConfig={{ scale: 140 }}
        height={height}
        style={{ width: '100%', height: '100%' }}
      >
        <ZoomableGroup zoom={1} center={[10, 20]} maxZoom={6}>
          <Geographies geography={TOPO_URL}>
            {({ geographies }) =>
              geographies.map((geo) => {
                const code = alpha3FromGeoId(geo.id);
                const metric = indexed[code];
                const v =
                  valueOverride && valueOverride[code] != null
                    ? valueOverride[code]
                    : metric
                      ? mode === 'count'
                        ? metric.count
                        : metric.sent_z ?? 0
                      : undefined;
                const fill = scale.color(v);
                const isSelected = selectedCode === code;
                const isHover = hovered === code;
                const title = metric
                  ? `${metric.country_name || code}: ${
                      mode === 'count'
                        ? metric.count.toLocaleString()
                        : (metric.sent_z ?? 0).toFixed(2) + ' (z)'
                    }`
                  : code;
                return (
                  <Tooltip key={geo.rsmKey} title={title}>
                    <Geography
                      geography={geo}
                      onMouseEnter={() => setHovered(code)}
                      onMouseLeave={() => setHovered(null)}
                      onClick={() => onSelect?.(code)}
                      style={{
                        default: {
                          fill,
                          stroke: '#fff',
                          strokeWidth: 0.5,
                          outline: 'none',
                        },
                        hover: {
                          fill,
                          stroke: '#1677ff',
                          strokeWidth: isHover ? 1.5 : 0.5,
                          cursor: 'pointer',
                          outline: 'none',
                        },
                        pressed: {
                          fill,
                          stroke: '#0958d9',
                          strokeWidth: 2,
                          outline: 'none',
                        },
                      }}
                      stroke={isSelected ? '#0958d9' : undefined}
                      strokeWidth={isSelected ? 1.5 : undefined}
                    />
                  </Tooltip>
                );
              })
            }
          </Geographies>
        </ZoomableGroup>
      </ComposableMap>
      <ChoroplethLegend scale={scale} />
    </div>
  );
}

function ChoroplethLegend({ scale }: { scale: ReturnType<typeof buildColorScale> }) {
  const stops = Array.from({ length: 8 }, (_, i) => i / 7);
  const [min, max] = scale.domain;
  return (
    <div style={{ padding: '4px 12px', display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
      <span style={{ color: '#666' }}>{scale.mode === 'count' ? '건수' : 'sent_z'}</span>
      <span>{scale.mode === 'count' ? '0' : min.toFixed(1)}</span>
      <div style={{ display: 'flex', flex: 1, maxWidth: 240, height: 8, borderRadius: 2, overflow: 'hidden' }}>
        {stops.map((t) => {
          const v = scale.mode === 'count' ? t * max : min + (max - min) * t;
          return <div key={t} style={{ flex: 1, background: scale.color(v) }} />;
        })}
      </div>
      <span>{scale.mode === 'count' ? max.toLocaleString() : max.toFixed(1)}</span>
    </div>
  );
}

export { alpha3FromGeoId };
