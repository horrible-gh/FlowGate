// What the toolbar needs to know about the attachment windows: whether the current Flow's window is open
// (the 첨부파일 button stays lit, final mockup `.haswindow`).
import { reactive } from 'vue'

export const flowAttachmentUi = reactive({ currentOpen: false })
