/** 0065 #33: only confirmed empty omitted instructions disappear. Unknown/read failures stay open. */
export interface InstructionBodyFacts {
  readonly docId: string | null
  readonly hasBody?: boolean
  readonly bodyState?: string | null
  readonly instructionOmitted?: boolean
}
export function instructionVisible(omitted: boolean, line: InstructionBodyFacts | null | undefined): boolean {
  if (!omitted) return true
  if (!line?.docId) return false
  if (line.bodyState != null && line.bodyState !== 'readable') return true
  return line.hasBody !== false
}
export function instructionSlotVisible(omitted: boolean, lines: readonly (InstructionBodyFacts & { readonly slot: string })[], fallbackId: string | null): boolean {
  return instructionVisible(omitted, lines.find(line => line.slot === 'instruction') ?? { docId: fallbackId })
}
