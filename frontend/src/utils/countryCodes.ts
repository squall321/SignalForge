// ISO 3166-1 alpha-2 → alpha-3 변환.
// charts country-distribution 은 alpha-2 (US/KR) 를 주는데,
// WorldChoropleth (world-atlas) 는 alpha-3 (USA/KOR) 를 쓴다.
// voc_active 에 실제 등장하는 27개국만 매핑 (필요 시 확장).
const ALPHA2_TO_ALPHA3: Record<string, string> = {
  AE: 'ARE', AU: 'AUS', BR: 'BRA', CA: 'CAN', CN: 'CHN', DE: 'DEU',
  ES: 'ESP', FR: 'FRA', GB: 'GBR', ID: 'IDN', IN: 'IND', IT: 'ITA',
  JP: 'JPN', KE: 'KEN', KR: 'KOR', MX: 'MEX', MY: 'MYS', NG: 'NGA',
  NL: 'NLD', PL: 'POL', RU: 'RUS', SE: 'SWE', TH: 'THA', TR: 'TUR',
  US: 'USA', VN: 'VNM', ZA: 'ZAF',
};

export function alpha2ToAlpha3(code: string): string {
  return ALPHA2_TO_ALPHA3[code?.toUpperCase()] ?? code;
}

const ALPHA3_TO_ALPHA2: Record<string, string> = Object.fromEntries(
  Object.entries(ALPHA2_TO_ALPHA3).map(([a2, a3]) => [a3, a2]),
);

export function alpha3ToAlpha2(code: string): string {
  return ALPHA3_TO_ALPHA2[code?.toUpperCase()] ?? code;
}
