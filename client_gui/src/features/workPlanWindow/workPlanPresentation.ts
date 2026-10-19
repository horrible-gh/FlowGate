/** Short document labels inside a named card; persisted types and step keys stay unchanged. */
export function workPlanDocumentNameKey(type: string, pairRole: string): string {
  if (type === 'TS') return 'document.scenario'
  if (type === 'TSR') return 'document.test_report'
  if (pairRole === 'instruction') return 'document.instruction'
  if (pairRole === 'result') return 'document.report'
  return 'document.design'
}
