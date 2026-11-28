import type { FlowGraphNode } from '../graph/flowGraph'
import { instructionVisible } from '../graph/instructionVisibility'
import { isTestCardLines } from '../graph/testCardLines'

export interface CardDocumentButton {
  docId: string
  docType: string | null
  title: string
  ownerSlot: string | null
  report: boolean
  /** The test card's 시나리오 button: it opens the card's scenario window (F81-9: a test card has no instruction button). */
  scenario?: boolean
  /** the server says the document has no body yet (`has_body` false; never M / WP). The button
   * looks disabled but still opens the document, so a person can start writing it from the card. */
  empty?: boolean
}
/** Line types that always have a body (O3 §4-1: M records, WP plans), as the Inspector's slotStateView decides; the
 * Center imports no Inspector part, so it keeps its own copy. */
const ALWAYS_FILLED_TYPES = ['M', 'WP']
const REPORT_TYPES = ['NR', 'TR', 'TR2', 'TSR']
function emptyLine(line: { docType: string | null; hasBody?: boolean }): boolean {
  return line.hasBody === false && !ALWAYS_FILLED_TYPES.includes(line.docType?.trim().toUpperCase() ?? '')
}
/** `scenario` tells the 시나리오 button (it opens the test card's scenario window). */
export interface CardDocumentOpen { nodeId: string; docId: string; scenario?: boolean }
/** Stable key of a button: the 시나리오 button keeps its own key apart from a document button of the same id. */
export const cardDocumentButtonKey = (doc: Pick<CardDocumentButton, 'docId' | 'scenario'>): string => doc.scenario ? `${doc.docId}#scenario` : doc.docId

/** Live graph metadata only; blank slots yield none.
 * F81-9: a test card (a 시나리오 line or a TS / TSR document) gets the Inspector's two buttons, 시나리오 · 레포트. Its
 * instruction line is never a button, even when the server still sends one: the TS document it carries is the 시나리오
 * (an old card without a 시나리오 line gets its 시나리오 button from it; a card with one ignores it, whatever its id). An old
 * card without lines whose document is a TS gets the 시나리오 button too, never a document button. */
export function cardDocumentButtons(node: Pick<FlowGraphNode, 'cardDocuments' | 'document'>): CardDocumentButton[] {
  const lines = node.cardDocuments ?? []
  const test = isTestCardLines(lines)
  const docs = new Map<string, CardDocumentButton>()
  const scenarios = new Map<string, CardDocumentButton>()
  const scenarioOf = (line: (typeof lines)[number]): CardDocumentButton => ({
    docId: line.docId!, docType: line.docType, title: line.title ?? line.docId!,
    ownerSlot: 'scenario', report: false, scenario: true, empty: emptyLine(line),
  })
  // An explicit 시나리오 line wins: the instruction line's TS document is then ignored, whatever its id.
  const scenarioLine = lines.some((line) => line.slot === 'scenario' && !!line.docId)
  for (const line of lines) {
    if (!line.docId) continue
    if (line.slot === 'scenario') { scenarios.set(line.docId, scenarioOf(line)); continue }
    if (test && line.slot === 'instruction') {
      if (!scenarioLine && line.docType?.trim().toUpperCase() === 'TS' && !scenarios.has(line.docId)) scenarios.set(line.docId, scenarioOf(line))
      continue
    }
    if (line.slot === 'instruction' && !instructionVisible(line.instructionOmitted === true, line)) continue
    const previous = docs.get(line.docId)
    if (previous && line.slot !== 'instruction') continue
    docs.set(line.docId, {
      docId: line.docId, docType: line.docType, title: line.title ?? line.docId,
      ownerSlot: line.slot, report: line.slot === 'report' || REPORT_TYPES.includes(line.docType ?? ''),
      empty: emptyLine(line),
    })
  }
  if (!docs.size && !scenarios.size && !lines.length && node.document?.docId) {
    const doc = node.document
    // An old card with no lines whose document is a TS: that document is the 시나리오 (it opens the scenario window).
    if (doc.typeCode?.trim().toUpperCase() === 'TS') {
      scenarios.set(doc.docId, { docId: doc.docId, docType: doc.typeCode, title: doc.title ?? doc.docId,
        ownerSlot: 'scenario', report: false, scenario: true, empty: false })
    } else {
      docs.set(doc.docId, { docId: doc.docId, docType: doc.typeCode, title: doc.title ?? doc.docId,
        ownerSlot: null, report: REPORT_TYPES.includes(doc.typeCode ?? ''), empty: false })
    }
  }
  const buttons: CardDocumentButton[] = [...docs.values()].filter((doc) => !scenarios.has(doc.docId) || doc.report)
  // The 시나리오 comes right before the report, as the Inspector's lines.
  for (const scenario of scenarios.values()) {
    const reportIndex = buttons.findIndex(button => button.report)
    buttons.splice(reportIndex < 0 ? buttons.length : reportIndex, 0, scenario)
  }
  return buttons
}
