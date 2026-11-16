// flowgate.v02gui.0033 0002-CH — the AI notification 「상세보기」 window is screen state of this tab, kept outside the
// notification popover. The popover closes on an outside press, and the window (teleported to body) is outside it:
// when the window was the AI tab's child, the first press on its title bar or resize handle closed the popover and
// took the window with it. The AI tab only opens it here; NotificationAiDetailHost (in the Designer header) draws it,
// so it stays to be moved and resized after the popover closes.
import { shallowRef } from 'vue'
import type { AiNotification } from './notificationApi'

/** The AI notification whose detail window is open, or null. One window at a time; a new open replaces it. */
export const aiNotificationDetail = shallowRef<AiNotification | null>(null)

export function openAiNotificationDetail(item: AiNotification): void {
  aiNotificationDetail.value = item
}

export function closeAiNotificationDetail(): void {
  aiNotificationDetail.value = null
}
