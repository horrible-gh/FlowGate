/**
 * Short document labels inside a named card; persisted types and step keys stay unchanged.
 * flowgate.v02.0074 T#3 (R §3): a design sheet is 「설계 지시」 (DS) or 「설계서」 (D / P / L / DB), the two
 * names the Designer's 설계 card gives its lines — never one 「설계 문서」.
 */
export function workPlanDocumentNameKey(type: string, pairRole: string): string {
  if (type === 'TS') return 'document.scenario'
  if (type === 'TSR') return 'document.test_report'
  if (pairRole === 'instruction') return 'document.instruction'
  if (pairRole === 'result') return 'document.report'
  if (type === 'DS') return 'document.design_instruction'
  return 'document.design'
}
