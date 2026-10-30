// The pre-role server fixtures still exercise their original field semantics across both
// relocated panels. Actual tab visibility and separation are covered by aiTab.0113.spec.ts.
import { defineComponent, h, type PropType } from 'vue'
import SettingsTab from '@/features/designer/inspector/features/settings/SettingsTab.vue'
import { provideCardSettings } from '@/features/designer/inspector/features/settings/useCardSettings'
import { inspectorSettingsKind, type InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
export default defineComponent({
  props: { target: { type: Object as PropType<InspectorTarget>, required: true } },
  setup(props) {
    provideCardSettings(() => props.target.nodeType !== null && inspectorSettingsKind(props.target) !== 'test' ? props.target : null)
    return () => h('div', [h(SettingsTab, { target: props.target }), ...(props.target.nodeType !== null ? [h(SettingsTab, { target: props.target, panel: 'ai' })] : [])])
  },
})
