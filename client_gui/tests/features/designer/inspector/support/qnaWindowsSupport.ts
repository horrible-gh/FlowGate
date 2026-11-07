// flowgate.v02.0150 — the Q&A ↗ windows are drawn by the Frame's window host (QnaWindowHost), apart from the Q&A tab, so
// a selection change never closes them. A spec that mounts the tab alone mounts it with the host, as the Frame lays them
// out; the windows are this tab's (module) list, closed between cases with closeAllQnaWindows.
import { defineComponent, h, type PropType } from 'vue'
import QnaTab from '@/features/designer/inspector/features/qna/QnaTab.vue'
import QnaWindowHost from '@/features/designer/inspector/features/qna/QnaWindowHost.vue'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'

export { closeAllQnaWindows } from '@/features/designer/inspector/features/qna/useQnaWindows'

export const QnaTabWithWindows = defineComponent({
  props: {
    target: { type: Object as PropType<InspectorTarget>, required: true },
    history: { type: Boolean, default: false },
  },
  setup: (props) => () => h('div', [h(QnaTab, { target: props.target, history: props.history }), h(QnaWindowHost, { target: props.target })]),
})
