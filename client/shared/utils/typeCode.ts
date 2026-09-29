/** Canonical FlowGate document type grammar. */
export const TYPE_CODE_RE = /^[A-Z][A-Z0-9]{0,3}$/
export function isValidTypeCode(value: string): boolean {
  return TYPE_CODE_RE.test(value)
}
export function docIdTypeCode(docId: string): string | null {
  const match = /\.(?:\d+)-([A-Z][A-Z0-9]{0,3})$/.exec(docId)
  return match ? match[1] : null
}
