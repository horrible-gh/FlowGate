// flowgate.v02.0051 — Inspector preview entry (fixture only, never part of the /app bundle).
// Same i18n as the app (shared instance + `gui` strings) and the app theme; no router, no Pinia,
// no transport: nothing here reaches a server.
import { createApp } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { inspectorRegistry, installInspectorMessages } from '@/features/designer/inspector/inspectorRegistry'
import '@/app/styles/theme.css'
import PreviewApp from './PreviewApp.vue'

registerAppMessages(i18n)
// Stage 2: the card parts are shown on their own, outside the Frame that merges their strings.
installInspectorMessages(i18n.global as unknown as Parameters<typeof installInspectorMessages>[0], inspectorRegistry)

const params = new URLSearchParams(window.location.search)
const locale = params.get('locale')
if (locale === 'ko' || locale === 'en' || locale === 'ja') i18n.global.locale.value = locale
else i18n.global.locale.value = 'ko'

// theme.css pins the app shell to the viewport; the gallery is a long page.
document.body.style.overflow = 'auto'

createApp(PreviewApp, { only: params.get('only') }).use(i18n).mount('#app')
