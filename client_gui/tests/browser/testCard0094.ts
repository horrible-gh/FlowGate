import { createApp, defineComponent, h } from 'vue'
import { createPinia } from 'pinia'
import TestSpecTab from '@/features/designer/inspector/features/testSpec/TestSpecTab.vue'
import TestCardWindows from '@/features/designer/inspector/features/testSpec/TestCardWindows.vue'
import { useTestCard } from '@/features/designer/inspector/features/testSpec/useTestCard'
import { prepareInspectorI18n, targetOf } from '../features/designer/inspector/support/inspectorSupport'
import { testSelection, TS_NODE } from '../features/designer/inspector/testSpec/testSpecFixtures'
import '@/app/styles/theme.css'
const id = new URLSearchParams(location.search).get('doc')!
const target = { ...targetOf(testSelection('graph', TS_NODE)), nodeId: 'gui-test', nodeType: 'document', cardDocType: 'TS',
  docId: id, docType: 'TS', docReviewStatus: 'draft', cardDocuments: [
    { slot: 'instruction', docType: 'TS', docId: id, title: 'Test', author: 'human', status: 'open', docReviewStatus: 'draft' },
    { slot: 'scenario', docType: 'TS', docId: id, title: 'Test', author: 'human', status: 'open', docReviewStatus: 'draft' },
  ] }
const i18n = prepareInspectorI18n()
i18n.global.locale.value = 'ko'
createApp(defineComponent({ setup() {
  const card = useTestCard(() => target as any)
  Object.assign(window, { testCard: card, testI18n: i18n })
  return () => h('main', { style: 'padding:20px;max-width:900px' }, [h(TestSpecTab, { target: target as any }), h(TestCardWindows, { target: target as any })])
} })).use(createPinia()).use(i18n).mount('#app')
