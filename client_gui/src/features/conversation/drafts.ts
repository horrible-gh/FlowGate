// flowgate.v02.0031 — unsent composer text per chat. Same browser key as the v0.1
// ConversationView (`flowgate.user.<user>.chat.drafts.<docId>`), so a draft typed in one
// generation of the screen is still there in the other. Best effort: storage failures never
// block typing or sending.

export function draftUserId(): string {
  try {
    const token = (window as unknown as { __accessToken__?: string }).__accessToken__
    if (!token) return 'guest'
    const payload = JSON.parse(atob(token.split('.')[1])) as { username?: string; sub?: string; user_id?: string }
    return String(payload.username ?? payload.sub ?? payload.user_id ?? 'guest')
  } catch {
    return 'guest'
  }
}

/** A fixed owner, including the user at the time typing/sending started. */
export interface DraftOwner {
  userId: string
  projectId: string
  groupId: string
  intentId: string
  chatId: string | null
}

export interface DraftSendSnapshot {
  owner: DraftOwner
  body: string
  revision: number
  createIntentId?: string
  createdChatId?: string
}

type DraftTarget = string | DraftOwner
const intentFallback = new Map<string, string>()
const freshIntent = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
const scopeKey = (scope: Pick<DraftOwner, 'userId' | 'projectId' | 'groupId'>) =>
  `flowgate.user.${scope.userId}.chat.draft-intents.${encodeURIComponent(scope.projectId)}.${encodeURIComponent(scope.groupId)}`

/** The latest unsent intent survives re-entry; [+] explicitly allocates a different one. */
export function draftIntent(scope: Pick<DraftOwner, 'userId' | 'projectId' | 'groupId'>, fresh = false): string {
  const key = `${scopeKey(scope)}.current`
  let intent = intentFallback.get(key)
  try { intent = localStorage.getItem(key) ?? intent } catch { /* best effort */ }
  if (fresh || !intent) intent = freshIntent()
  intentFallback.set(key, intent)
  try { localStorage.setItem(key, intent) } catch { /* best effort */ }
  return intent
}

/** Separate from the draft id, but stable for retries/re-entry of that same draft. */
export function draftCreateIntent(owner: DraftOwner): string {
  const key = `${scopeKey(owner)}.${encodeURIComponent(owner.intentId)}.create-intent`
  let intent = intentFallback.get(key)
  try { intent = localStorage.getItem(key) ?? intent } catch { /* best effort */ }
  if (!intent) intent = `chat-${freshIntent()}`
  intentFallback.set(key, intent)
  try { localStorage.setItem(key, intent) } catch { /* best effort */ }
  return intent
}

export function draftKey(target: DraftTarget): string {
  if (typeof target === 'string') return `flowgate.user.${draftUserId()}.chat.drafts.${target}`
  // Confirmed CH keys retain the v0.1 format and existing values are never migrated.
  if (target.chatId) return `flowgate.user.${target.userId}.chat.drafts.${target.chatId}`
  return `${scopeKey(target)}.${encodeURIComponent(target.intentId)}.text`
}

export function loadDraft(target: DraftTarget | null): string {
  if (!target) return ''
  try {
    return localStorage.getItem(draftKey(target)) ?? ''
  } catch {
    return ''
  }
}

export function saveDraft(target: DraftTarget | null, value: string): void {
  if (!target) return
  try {
    if (value === '') localStorage.removeItem(draftKey(target))
    else localStorage.setItem(draftKey(target), value)
  } catch {
    /* best effort */
  }
}
