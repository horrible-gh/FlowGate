// flowgate.v02.0056 F2-2 — the state and the one call of a [작성] window (O3 0009-TR §4-2): the text being written,
// whether it may be sent, and [저장] — cardLineWrite with the content slotWriteModel decides. After the 201 the
// window closes, the answer's line stands for the card's line until the Canvas read shows it, and the opener hears
// it (`written`). A refusal that means the line has a document now opens that document (at once when the refusal
// names it, else once the Canvas read shows it) and tells the opener (`reread`); nothing more is written then.
import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import { cardLineErrorOf, cardLineWrite } from '@/features/document'
import { openDocumentWindow } from './documentWindows'
import { slotWriteContent, slotWriteContentless, slotWriteFailure, slotWriteProblem, type SlotWriteFailure } from './slotWriteModel'
import { closeSlotWriteWindow, openWhenShown, recordWrittenCardLine, type SlotWriteRequest, type SlotWriteWindowState } from './slotWriteWindows'

export interface SlotWriteState {
  readonly request: ComputedRef<SlotWriteRequest>
  /** A work plan line: made without text. */
  readonly contentless: ComputedRef<boolean>
  readonly text: Ref<string>
  readonly saving: Readonly<Ref<boolean>>
  readonly failure: Readonly<Ref<SlotWriteFailure | null>>
  /** The line has a document now: nothing more is written from here. */
  readonly existing: ComputedRef<boolean>
  /** A content problem seen before sending (`content.too_long`), or null. */
  readonly problemKey: ComputedRef<string | null>
  save(): Promise<void>
}

export function useSlotWrite(win: () => SlotWriteWindowState): SlotWriteState {
  const request = computed(() => win().request)
  const contentless = computed(() => slotWriteContentless(request.value.docType))
  const text = ref('')
  const saving = ref(false)
  const failure = ref<SlotWriteFailure | null>(null)
  const existing = computed(() => failure.value?.kind === 'existing')
  const problemKey = computed(() => {
    const problem = slotWriteProblem(text.value, request.value.docType)
    return problem ? `content.${problem}` : null
  })

  // The text being written is a new edit: an earlier refusal of the content no longer stands.
  watch(text, () => {
    if (failure.value?.kind === 'field') failure.value = null
  })

  async function save(): Promise<void> {
    if (saving.value || existing.value || problemKey.value !== null) return
    const current = request.value
    const key = win().key
    saving.value = true
    failure.value = null
    try {
      const result = await cardLineWrite(current.flowId, current.nodeKey, current.slot, slotWriteContent(text.value, current.docType))
      if (result.line) recordWrittenCardLine(current.flowId, current.nodeKey, result.line)
      closeSlotWriteWindow(key)
      current.written?.(result)
    } catch (error) {
      const refused = slotWriteFailure(cardLineErrorOf(error))
      failure.value = refused
      if (refused.kind === 'existing') {
        if (refused.docId) openDocumentWindow(refused.docId)
        else openWhenShown(current.flowId, current.nodeKey, current.slot)
        current.reread?.()
      }
    } finally {
      saving.value = false
    }
  }

  return { request, contentless, text, saving, failure, existing, problemKey, save }
}
