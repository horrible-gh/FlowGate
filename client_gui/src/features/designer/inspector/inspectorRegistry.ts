// flowgate.v02.0048 T#1 — Inspector feature registry (D#1 §5). Every folder
// `features/<key>/register.ts` default-exports one registration; this module collects them all
// (eager import.meta.glob), checks the contract once (key = folder name, unique key and order,
// condition values inside their sets) and throws on the first broken one — in development, tests
// and production alike, since the set is static. The Frame reads the registry only: it knows no
// feature by name. A feature appears by adding its folder; nothing here is edited for it.
//
// Collecting is split into a pure function over glob results (createInspectorRegistry, tested with
// fixture modules) and the module registry built from the real glob (inspectorRegistry).
import type { Component, Ref } from 'vue'
import { INSPECTOR_TABS, type InspectorTabKey } from './inspectorTabs'
import {
  INSPECTOR_CARD_DOC_TYPES,
  INSPECTOR_CARD_KINDS,
  INSPECTOR_DOC_TYPES,
  INSPECTOR_OWNERSHIPS,
  INSPECTOR_SETTINGS_KINDS,
  inspectorSettingsKind,
  type InspectorCardKind,
  type InspectorSettingsKind,
  type InspectorTarget,
} from './inspectorTarget'

/** Locales every feature's `messages.ts` carries, with the same key structure. */
export const INSPECTOR_LOCALES = ['ko', 'en', 'ja'] as const

export type InspectorMessages = Readonly<Record<(typeof INSPECTOR_LOCALES)[number], Readonly<Record<string, unknown>>>>

/**
 * Tab number supplier (e.g. waiting questions): given the target — null whenever the registration
 * does not apply to the selected Node — it answers a number, or null for none.
 */
export type InspectorTabCount = (target: Readonly<Ref<InspectorTarget | null>>) => Readonly<Ref<number | null>>

export interface InspectorTabContribution {
  readonly key: InspectorTabKey
  /** Tab Content part; its one input is `target`. */
  readonly component: Component
  readonly count?: InspectorTabCount
  /**
   * flowgate.v02.0053 T#6 — the Content part takes the tab body's free height (its box reaches the bottom
   * of the tab even with little content), e.g. the attachments drop area highlighted as a whole.
   */
  readonly fill?: boolean
}

export interface InspectorRegistration {
  /** Feature id = its folder name under features/. */
  readonly key: string
  /** Display order within a place (tab Content, header actions, header meta); unique overall. */
  readonly order: number
  /** Card kinds the feature applies to; empty or absent = every kind. */
  readonly cardKinds?: readonly InspectorCardKind[]
  /** Narrows `cardKinds` by document type; empty or absent = every type of those kinds. */
  readonly docTypes?: readonly string[]
  /** Flow ownerships the feature applies to; empty or absent = every ownership. */
  readonly ownerships?: readonly string[]
  /** The feature needs a document bound to the Node. */
  readonly requiresDocument: boolean
  /** The feature needs a v0.1 sequence slot on the Node (a sequence row); absent = no. */
  readonly requiresSlot?: boolean
  /**
   * flowgate.v02.0053 T#7 — settings kinds (inspectorSettingsKind) the feature applies to; empty or absent =
   * every Node. A settings tab part names the card settings it shows (a Graph card's T#2 kind).
   */
  readonly settingsKinds?: readonly InspectorSettingsKind[]
  readonly tab?: InspectorTabContribution
  /** Header action part (right of the state chip); its one input is `target`. */
  readonly headerAction?: Component
  /** Header meta part (left of the state chip); its one input is `target`. */
  readonly headerMeta?: Component
  /**
   * flowgate.v02.0051 — header kind badge part: replaces the static 종류 badge (the R/B 종류 배지 ▾ of
   * kindSwitch). The Frame lays out the first that applies; its one input is `target`.
   */
  readonly headerKind?: Component
  /** Replaces the default state pill for the first matching feature. */
  readonly headerState?: Component
  /**
   * flowgate.v02.0051 — ⋯ menu part: the card's own items after the common ID 복사 (실행 이력 · ■ 중단 ·
   * ↻ 재실행 …). Its inputs are `target` and `close` (closes the menu).
   */
  readonly headerMenu?: Component
  /**
   * flowgate.v02.0053 T#9 — the header ✎ 메모 part: replaces the Frame's dim, disabled ✎ 메모 (R0052 §2.6). The
   * Frame lays out the first that applies; its one input is `target`.
   */
  readonly headerMemo?: Component
  /** Inline editor below the header, independent of the selected tab. */
  readonly headerPanel?: Component
  readonly messages: InspectorMessages
}

export interface InspectorRegistry {
  /** Registrations in `order`. */
  readonly entries: readonly InspectorRegistration[]
}

const FOLDER_OF = /(?:^|\/)features\/([^/]+)\/register\.ts$/

const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)

function listViolations(label: string, field: string, value: unknown, allowed: readonly string[]): string[] {
  if (value === undefined) return []
  if (!Array.isArray(value)) return [`${label}: ${field} is not a list`]
  return value.filter((item) => !allowed.includes(item)).map((item) => `${label}: ${field} has unknown value '${String(item)}'`)
}

/**
 * Contract violations of collected `register.ts` modules (glob path -> module), one line each,
 * naming the folder. Empty = the registry may be built.
 */
export function registrationViolations(modules: Readonly<Record<string, unknown>>): string[] {
  const out: string[] = []
  const keys = new Map<string, string>()
  const orders = new Map<number, string>()
  for (const path of Object.keys(modules).sort()) {
    const folder = FOLDER_OF.exec(path.replace(/\\/g, '/'))?.[1] ?? path
    const label = `features/${folder}`
    const entry = (modules[path] as { default?: unknown } | null)?.default
    if (!isObject(entry)) {
      out.push(`${label}: register.ts has no default export registration`)
      continue
    }
    if (entry.key !== folder) out.push(`${label}: key '${String(entry.key)}' differs from the folder name`)
    if (typeof entry.key === 'string') {
      const seen = keys.get(entry.key)
      if (seen) out.push(`${label}: key '${entry.key}' is already registered by ${seen}`)
      else keys.set(entry.key, label)
    }
    if (typeof entry.order !== 'number' || !Number.isFinite(entry.order)) {
      out.push(`${label}: order is not a number`)
    } else {
      const seen = orders.get(entry.order)
      if (seen) out.push(`${label}: order ${entry.order} is already used by ${seen}`)
      else orders.set(entry.order, label)
    }
    out.push(...listViolations(label, 'cardKinds', entry.cardKinds, INSPECTOR_CARD_KINDS))
    out.push(...listViolations(label, 'ownerships', entry.ownerships, INSPECTOR_OWNERSHIPS))
    out.push(...listViolations(label, 'settingsKinds', entry.settingsKinds, INSPECTOR_SETTINGS_KINDS))
    const cardKinds = Array.isArray(entry.cardKinds) ? (entry.cardKinds as string[]) : []
    const ownTypes = cardKinds.length
      ? cardKinds.flatMap((kind) => INSPECTOR_CARD_DOC_TYPES[kind as keyof typeof INSPECTOR_CARD_DOC_TYPES] ?? [])
      : INSPECTOR_DOC_TYPES
    const typeViolations = listViolations(label, 'docTypes', entry.docTypes, INSPECTOR_DOC_TYPES)
    out.push(...typeViolations)
    if (!typeViolations.length && Array.isArray(entry.docTypes)) {
      for (const type of entry.docTypes as string[]) {
        if (!ownTypes.includes(type)) out.push(`${label}: docTypes '${type}' is not a type of cardKinds [${cardKinds.join(', ')}]`)
      }
    }
    if (typeof entry.requiresDocument !== 'boolean') out.push(`${label}: requiresDocument is not a boolean`)
    if (entry.requiresSlot !== undefined && typeof entry.requiresSlot !== 'boolean') out.push(`${label}: requiresSlot is not a boolean`)
    const tab = entry.tab
    if (tab !== undefined) {
      if (!isObject(tab) || !INSPECTOR_TABS.includes(tab.key as InspectorTabKey)) {
        out.push(`${label}: tab key '${isObject(tab) ? String(tab.key) : String(tab)}' is not an Inspector tab`)
      } else {
        if (!tab.component) out.push(`${label}: tab has no component`)
        if (tab.count !== undefined && typeof tab.count !== 'function') out.push(`${label}: tab count is not a function`)
        if (tab.fill !== undefined && typeof tab.fill !== 'boolean') out.push(`${label}: tab fill is not a boolean`)
      }
    }
    if (!tab && !entry.headerAction && !entry.headerMeta && !entry.headerKind && !entry.headerState && !entry.headerMenu && !entry.headerMemo) {
      out.push(`${label}: contributes no tab, header action, header meta, header kind, header menu or header memo`)
    }
    const messages = entry.messages
    for (const locale of INSPECTOR_LOCALES) {
      if (!isObject(messages) || !isObject(messages[locale])) out.push(`${label}: messages has no '${locale}' block`)
    }
  }
  return out
}

/** Builds a registry from collected modules; throws on any contract violation. */
export function createInspectorRegistry(modules: Readonly<Record<string, unknown>>): InspectorRegistry {
  const violations = registrationViolations(modules)
  if (violations.length) throw new Error(`Inspector registry: ${violations.join('; ')}`)
  const entries = Object.values(modules)
    .map((module) => (module as { default: InspectorRegistration }).default)
    .sort((a, b) => a.order - b.order)
  return { entries }
}

/** The registry of the real feature folders. */
export const inspectorRegistry: InspectorRegistry = createInspectorRegistry(
  import.meta.glob('./features/*/register.ts', { eager: true }),
)

/** Whether a registration's declared conditions all hold for the target (D#1 §5.3 rule 1). */
export function registrationApplies(entry: InspectorRegistration, target: InspectorTarget): boolean {
  if (entry.requiresDocument && !target.docId) return false
  if (entry.requiresSlot && !target.slot) return false
  if (entry.cardKinds?.length && !entry.cardKinds.includes(target.cardKind)) return false
  const docType = target.docType?.trim().toUpperCase() ?? null
  if (entry.docTypes?.length && !(docType && entry.docTypes.includes(docType))) return false
  if (entry.ownerships?.length && !(target.ownership && entry.ownerships.includes(target.ownership))) return false
  if (entry.settingsKinds?.length) {
    const kind = inspectorSettingsKind(target)
    if (!(kind && entry.settingsKinds.includes(kind))) return false
  }
  return true
}

/** The registrations shown for a target, in `order`; none without a target. */
export function contributionsFor(registry: InspectorRegistry, target: InspectorTarget | null): readonly InspectorRegistration[] {
  return target ? registry.entries.filter((entry) => registrationApplies(entry, target)) : []
}

interface MessageTarget {
  mergeLocaleMessage(locale: string, message: Record<string, unknown>): void
}

/**
 * Merges every registration's messages into an i18n composer under `gui.inspector.<key>`
 * (D#1 §5.5). The caller hands the target (the Frame: the global composer; tests: i18n.global),
 * so this module never reaches the shared i18n instance. Calling it again changes nothing.
 */
export function installInspectorMessages(target: MessageTarget, registry: InspectorRegistry = inspectorRegistry): void {
  for (const locale of INSPECTOR_LOCALES) {
    const inspector = Object.fromEntries(registry.entries.map((entry) => [entry.key, entry.messages[locale]]))
    if (Object.keys(inspector).length) target.mergeLocaleMessage(locale, { gui: { inspector } })
  }
}
