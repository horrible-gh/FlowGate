// flowgate.v02.0088 (D0004 §2.1-2 / §3.1): a UI message kept as its meaning — an i18n key and
// its interpolation params — and translated only when it is rendered, so a toast, an error line
// or an open window that outlives a language switch shows the new language. The server's own
// words (a diagnostic it sent in the x-locale of that request), document titles and user input
// are kept as a separate raw string and are never translated.

export interface UiKeyText {
  readonly key: string
  readonly params?: Readonly<Record<string, unknown>>
}

export interface UiRawText {
  readonly raw: string
}

export type UiText = UiKeyText | UiRawText

export function uiKey(key: string, params?: Readonly<Record<string, unknown>>): UiKeyText {
  return params ? { key, params } : { key }
}

export function uiRaw(raw: string): UiRawText {
  return { raw }
}

export type UiTranslate = (key: string, params?: Record<string, unknown>) => string

/** The text to show now: a key in the current locale, a raw string as it came. */
export function renderUiText(t: UiTranslate, text: UiText | null | undefined): string {
  if (!text) return ''
  if ('raw' in text) return text.raw
  return text.params ? t(text.key, { ...text.params }) : t(text.key)
}

/**
 * A failed request's text: the thrown Error's own message when it carries one (the adapters in
 * this app leave only the server's own diagnostic there), otherwise the operation's localized
 * fallback, kept as a key.
 */
export function failureText(error: unknown, fallbackKey: string, params?: Readonly<Record<string, unknown>>): UiText {
  const message = error instanceof Error ? error.message.trim() : ''
  return message ? uiRaw(message) : uiKey(fallbackKey, params)
}
