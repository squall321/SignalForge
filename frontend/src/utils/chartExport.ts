// P4.2 R6 트랙 D — 차트 PNG 다운로드 + 데이터 JSON 복사 유틸.
// echarts-for-react 의 ref.getEchartsInstance() 에서 dataURL 추출.
// 외부 의존성 없음.

/** echarts instance(any) 와 파일명을 받아 PNG 로 다운로드. */
export function downloadChartPng(echartsInstance: unknown, filename: string): boolean {
  try {
    const inst = echartsInstance as { getDataURL?: (opt: Record<string, unknown>) => string } | null;
    if (!inst || typeof inst.getDataURL !== 'function') return false;
    const url = inst.getDataURL({
      type: 'png',
      pixelRatio: 2,
      backgroundColor: '#ffffff',
    });
    const a = document.createElement('a');
    a.href = url;
    a.download = filename.endsWith('.png') ? filename : `${filename}.png`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    return true;
  } catch {
    return false;
  }
}

/** 객체를 JSON 문자열로 클립보드 복사. 성공 시 true. */
export async function copyJsonToClipboard(data: unknown): Promise<boolean> {
  try {
    const text = JSON.stringify(data, null, 2);
    if (typeof navigator === 'undefined' || !navigator.clipboard) return false;
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** 파일명 안전화 — 공백/특수문자를 -로 치환. */
export function safeFilename(name: string): string {
  return name
    .replace(/[^\p{L}\p{N}_-]+/gu, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 80) || 'chart';
}
