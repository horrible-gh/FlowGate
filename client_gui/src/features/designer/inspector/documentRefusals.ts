// flowgate.v02test.0090 — the server's "no" for changing a card's documents, as one sentence per action, shared by every
// editing entry point of the Inspector (✎, [작성], the memo ✎ / [+] / trash). The Frame provides it once for the card's
// project (provideDocumentRefusals); an entry point reads it (useDocumentRefusals) and goes off with the sentence as its tooltip.
//
// Each value is the server's refusal of one permission id, worded by the shared denial sentences (role / rule from the
// server's answer, never from a role guess), or null when the server allowed it OR has not answered yet — a missing answer
// never turns a button off (the server still checks every request; its 403 shows the common box).
import { computed, inject, provide, type ComputedRef, type InjectionKey } from 'vue'
import { useI18n } from 'vue-i18n'
import { projectRefusal, projectRole } from '@shared/permissions/capabilities'
import { denialLines } from '@shared/permissions/denial'

export interface DocumentRefusals {
  /** ✎ (edit a document's body), a memo's ✎: `project.document.update`. */
  readonly update: ComputedRef<string | null>
  /** [작성] (a new document on a card line), a new memo: `project.document.create`. */
  readonly create: ComputedRef<string | null>
  /** A memo's trash icon: `project.document.delete`. */
  readonly remove: ComputedRef<string | null>
}

const DOCUMENT_REFUSALS: InjectionKey<DocumentRefusals> = Symbol('flowgate.documentRefusals')

type Translate = (key: string, params?: Record<string, unknown>) => string

/**
 * The server's refusal of one document permission of `projectId`, as a sentence, or null (allowed, or no answer yet; the
 * answer is asked for once the project is known). Plain function: read it inside a computed / getter and it follows the store.
 */
export function documentRefusalOf(projectId: string | null | undefined, action: string, t: Translate): string | null {
  const decision = projectRefusal(projectId ?? null, action)
  if (!decision) return null
  return denialLines([{ action, decision }], projectRole(projectId ?? null), (key, params) => t(key, params ?? {}))[0] ?? null
}

/** `project.document.update` of `projectId` — what refuses ✎ and what refuses the work plan window opened from [업로드]. */
export const DOCUMENT_UPDATE = 'project.document.update'

/** The refusals of `projectId`'s documents (reactive; the project answer is asked for once it is known). */
export function documentRefusalsOf(projectId: () => string | null | undefined): DocumentRefusals {
  const { t } = useI18n()
  const sentence = (action: string) => computed<string | null>(() => documentRefusalOf(projectId(), action, (key, params) => t(key, params ?? {})))
  return { update: sentence(DOCUMENT_UPDATE), create: sentence('project.document.create'), remove: sentence('project.document.delete') }
}

/** The Frame (DesignerInspector) provides the refusals of the shown card's project to everything below it. */
export function provideDocumentRefusals(projectId: () => string | null | undefined): DocumentRefusals {
  const refusals = documentRefusalsOf(projectId)
  provide(DOCUMENT_REFUSALS, refusals)
  return refusals
}

const NONE: DocumentRefusals = {
  update: computed(() => null),
  create: computed(() => null),
  remove: computed(() => null),
}

/** What an entry point reads; without a Frame above it nothing is refused (the server still decides). */
export function useDocumentRefusals(): DocumentRefusals {
  return inject(DOCUMENT_REFUSALS, NONE)
}
