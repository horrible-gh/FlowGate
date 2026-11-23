// The waiting AI attachment requests of the open Flow, for the badge of the 📎 button in the chat input
// (v02test.0074 card 1). It is the same number the Flow's attachment window shows in its "승인 대기 N건" band: the
// server's Flow-level count (`GET /attachment-requests/counts?flow_id=`, pending + approving over every chat and every
// card of the Flow), not what one open chat happens to hold. So a chat switch never changes it, and a request made by
// another chat or card of the Flow is in it.
// The count is read when the Flow becomes the target and again after every `attachment_request_updated` event of that
// Flow (the event is only a signal, the server is read again) and after a reconnect of the project event stream.
import { ref, watch, type Ref } from 'vue'
import { attachmentRequestCounts } from '@/features/document/api/attachmentRequests'
import { useInspectorEvents } from '../inspector/useInspectorResource'

export interface FlowAttachmentPendingScope {
  projectId: string
  flowId: string
}

export function useFlowAttachmentPending(scope: () => FlowAttachmentPendingScope | null): Ref<number> {
  const pending = ref(0)
  // answers are applied in request order: a read answered after the Flow changed or after a newer read is dropped.
  let latest = 0

  async function read(): Promise<void> {
    const current = scope()
    const mine = ++latest
    if (current === null) {
      pending.value = 0
      return
    }
    try {
      const counts = await attachmentRequestCounts(current.flowId)
      if (mine === latest) pending.value = counts.total
    } catch {
      // a failed read keeps the number on screen; the next event or reconnect reads again.
    }
  }

  // another Flow starts from nothing instead of showing the previous Flow's number while it is read.
  watch(
    () => { const current = scope(); return current ? `${current.projectId}\n${current.flowId}` : null },
    () => { pending.value = 0; void read() },
    { immediate: true },
  )
  useInspectorEvents(() => { const current = scope(); return current ? { projectId: current.projectId, flowId: current.flowId, docId: null } : null }, {
    events: ['attachment_request_updated'],
    matches: (frame, current) => frame.groupId === current.flowId,
    changed: () => { void read() },
  })
  return pending
}
