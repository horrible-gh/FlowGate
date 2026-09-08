// flowgate.default.0481 T0010 rev4 — entry for merge-approve-uuid.0481.mjs.
//
// Mounts the real GitMergeReviewDialog with the real @shared/api stack, so the approve
// request that leaves this page is the same request the deployed app sends. Nothing is
// stubbed in the page: the harness intercepts at the network layer (CDP Fetch) instead,
// which is the only way to see the exact bytes the browser puts on the wire.
import { createApp } from 'vue'
import i18n from '@shared/i18n'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'

const app = createApp(GitMergeReviewDialog, {
  groupId: 'test2.default.0009',
  mergeId: 4,
  branch: 'test2_default_0009',
  baseBranch: 'main',
  providers: [{ id: 'p1', name: 'Claude Haiku 4.5' }],
  selectedProvider: 'p1',
})
app.use(i18n)
app.mount('#app')

// The harness reads these to prove which browser branch actually ran.
Object.assign(window as unknown as Record<string, unknown>, {
  __fgSecureContext: window.isSecureContext,
  __fgHasRandomUUID: typeof crypto !== 'undefined' && typeof crypto.randomUUID,
})
