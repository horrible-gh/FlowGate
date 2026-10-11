// flowgate.v02.0053 T#5 — the 조사 card's settings value its lines follow: a card whose settings omit the
// instruction (T#2 `instruction` = omit) has no 지시서 line (v7). Read when the card is selected — no server
// event announces a settings change, and the settings tab is another tab, so coming back reads again. A
// Node the server keeps no settings for (a document Node, a card not saved yet) or a failed read keeps the
// line.
import { ref, watch, type Ref } from 'vue'
import { settingsInstructionOmitted, settingsNodeRead } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'

export interface ResearchCardState {
  readonly instructionOmitted: Readonly<Ref<boolean>>
}

/** The stored card behind the target (flow \n node), or null: a document Node or a `tmp_` card. */
const cardOf = (target: InspectorTarget | null) =>
  target && target.nodeType !== null && !target.nodeId.startsWith('tmp_') ? `${target.flowId}\n${target.nodeId}` : null

export function useResearchCard(target: () => InspectorTarget | null): ResearchCardState {
  const instructionOmitted = ref(false)
  watch(
    () => cardOf(target()),
    (card, _previous, onCleanup) => {
      let current = true
      onCleanup(() => {
        current = false
      })
      instructionOmitted.value = false
      if (card === null) return
      const [flowId, nodeKey] = card.split('\n')
      settingsNodeRead(flowId, nodeKey).then(
        (node) => {
          if (current) instructionOmitted.value = settingsInstructionOmitted(node)
        },
        () => undefined,
      )
    },
    { immediate: true },
  )
  return { instructionOmitted }
}
