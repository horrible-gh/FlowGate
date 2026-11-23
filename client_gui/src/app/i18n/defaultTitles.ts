import { APP_MESSAGES } from './index'

// Default names are display-only comparisons: a stored name may have been made under any supported screen language
// (a Flow created on a KO screen and opened on a JA one still shows 「새 플로우」), so a name counts as the default when it
// equals that message in any of ko / en / ja. Stored names are never changed here.

/** Whether `title` equals the message at `key` (a `gui.`-rooted i18n key) in any supported language. */
export function isDefaultMessageTitle(title: string | null | undefined, key: string): boolean {
  if (typeof title !== 'string') return false
  const normalized = title.trim()
  return Object.values(APP_MESSAGES).some((messages) => {
    let label: unknown = { gui: messages }
    for (const part of key.split('.')) {
      if (!label || typeof label !== 'object') return false
      label = (label as Record<string, unknown>)[part]
    }
    return typeof label === 'string' && normalized === label.trim()
  })
}

/** The i18n key of a new Flow's provisional name (새 플로우 / New Flow / 新規フロー). */
export const DEFAULT_FLOW_NAME_KEY = 'gui.designer.file_menu.flow_new_title'
/** The i18n key of a new chat's provisional title (새 대화 / New conversation / 新しい会話). */
export const DEFAULT_CHAT_TITLE_KEY = 'gui.conversation.header.new_title'

/** A Flow name that is still the provisional one, in any supported language. */
export function isDefaultFlowName(name: string | null | undefined): boolean {
  return isDefaultMessageTitle(name, DEFAULT_FLOW_NAME_KEY)
}

/** A chat title that is still the provisional one, in any supported language. */
export function isDefaultChatTitle(title: string | null | undefined): boolean {
  return isDefaultMessageTitle(title, DEFAULT_CHAT_TITLE_KEY)
}
