// F81-9 (0017-T): a test (TS) card has no instruction. Its lines are 시나리오 · 레포트 only, on the Canvas and in the
// Inspector alike. The server may still send an instruction line for the TS document (before the parallel server card
// lands, or on old data): the screen never shows it. The Center imports no Inspector part, so both read this file.

export interface TestCardLineFacts {
  readonly slot: string
  readonly docId: string | null
  readonly docType?: string | null
}

const TEST_TYPES = ['TS', 'TSR']
const typeOf = (line: Pick<TestCardLineFacts, 'docType'>) => line.docType?.trim().toUpperCase() ?? ''

/** A test card's lines: a 시나리오 line, or a line carrying a TS / TSR document. */
export function isTestCardLines(lines: readonly TestCardLineFacts[] | null | undefined): boolean {
  return !!lines?.some((line) => line.slot === 'scenario' || TEST_TYPES.includes(typeOf(line)))
}

/**
 * The slot the server keeps the TS document in, for the document API path (`/slots/{slot}/document`) and the document
 * decision only — never a screen name. While an instruction line still carries the TS document the server stores it
 * there (a 시나리오 write is refused 422 slot_shares_a_document); otherwise the 시나리오 line owns it.
 */
export function testScenarioStorageSlot(lines: readonly TestCardLineFacts[] | null | undefined, docId: string | null | undefined): 'instruction' | 'scenario' {
  return !!docId && !!lines?.some((line) => line.slot === 'instruction' && line.docId === docId) ? 'instruction' : 'scenario'
}
