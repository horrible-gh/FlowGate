// What the toolbar needs to know about the attachment windows: whether the current Flow's window is open
// (the attachments button stays lit, final mockup `.haswindow`).
import { reactive } from 'vue'

// `toggle` is set by the Host while it is mounted: opens the current Flow's window, or closes it when it is open (the
// 📎 button of the chat input uses it; the Host's own `open` is still what the toolbar button calls).
export const flowAttachmentUi = reactive<{ currentOpen: boolean; toggle: (() => void) | null }>({ currentOpen: false, toggle: null })
