// flowgate.v02.0031 — unsent composer text per chat. Same browser key as the v0.1
// ConversationView (`flowgate.user.<user>.chat.drafts.<docId>`), so a draft typed in one
// generation of the screen is still there in the other. Best effort: storage failures never
// block typing or sending.

function draftUserId(): string {
  try {
    const token = (window as unknown as { __accessToken__?: string }).__accessToken__
    if (!token) return 'guest'
    const payload = JSON.parse(atob(token.split('.')[1])) as { username?: string; sub?: string; user_id?: string }
    return String(payload.username ?? payload.sub ?? payload.user_id ?? 'guest')
  } catch {
    return 'guest'
  }
}

export function draftKey(chatId: string): string {
  return `flowgate.user.${draftUserId()}.chat.drafts.${chatId}`
}

export function loadDraft(chatId: string | null): string {
  if (!chatId) return ''
  try {
    return localStorage.getItem(draftKey(chatId)) ?? ''
  } catch {
    return ''
  }
}

export function saveDraft(chatId: string | null, value: string): void {
  if (!chatId) return
  try {
    if (value === '') localStorage.removeItem(draftKey(chatId))
    else localStorage.setItem(draftKey(chatId), value)
  } catch {
    /* best effort */
  }
}
