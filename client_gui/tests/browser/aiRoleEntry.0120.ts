// Browser fixture: product components and API adapter, isolated API transport supplied by pytest.
import { createApp, defineComponent, h, onMounted, ref } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import { listAiGroups } from '@/features/ai/api'
import type { AiGroup } from '@/features/ai/types'
import AiGroupsPanel from '@/features/ai/groups/AiGroupsPanel.vue'
import '@/app/styles/theme.css'
import '@/features/ai/providers/aiCards.css'

registerAppMessages(i18n)
i18n.global.locale.value = 'ko'
setCurrentUser({ username: 'isolated-admin', email: null, isAdmin: true })
const app = defineComponent({ setup() {
  const groups = ref<AiGroup[]>([])
  const loaded = ref(false)
  const error = ref('')
  async function reload() {
    try { groups.value = await listAiGroups(); loaded.value = true }
    catch (e) { error.value = String(e) }
  }
  onMounted(reload)
  return () => h('main', { style: 'padding: 24px; max-width: 1250px; margin: auto;' }, [
    h('p', { 'data-fixture': true }, '격리 브라우저 시험 · 운영 서비스 아님'),
    error.value ? h('p', { role: 'alert' }, error.value) : loaded.value
      ? h(AiGroupsPanel, { providers: [], initialGroups: groups.value, onChanged: reload })
      : h('p', 'Loading'),
  ])
} })
createApp(app).use(i18n).mount('#app')
