// flowgate.v02.0048 T#9 — the settings tab's state (D#1 §7.2 / §7.3, D#2 §2.4). The v0.1 sequence belongs
// to the Flow, so the resource read through useInspectorResource is keyed by the Flow id: it reads the
// Flow's sequence root and rows (`settingsRead`), the group's live AI run and whether the group was
// disposed (the facts the server refuses on). The Node's row is the one its slot names.
// Events (D#1 §7.4): a sequence save broadcasts group_view_refresh (+ doc_review_status_changed when the
// root's status moved); a hop starting / ending a row sends ai_invoke_started / ai_invoke_finished and
// document_explorer_refresh. Any of them about the Flow reads the rows again.
// Saving (D#2 §2.4 rules 2 / 4-6): nothing is sent when nothing changed; the rows are read again right
// before the PATCH and every row goes back as read with only the Node's row's settings changed. Success
// or refusal, the rows are read again before it ends; a success then asks the Host to read the Canvas
// again (`refresh-flow`): the rows written again carry new ids, and the Canvas keeps each slot's
// node_key while its slot takes the new id. A refusal of the group guard (disposed, AI run) keeps the
// fields disabled until the Node changes or the state is read again; a stale screen (the rows moved)
// reads the Canvas again as well.
import { computed, inject, ref, watch, type ComputedRef, type Ref } from 'vue'
import {
  reviewDocumentRead,
  settingsChoicesRead,
  settingsErrorOf,
  settingsRead,
  settingsRunRead,
  settingsSave,
  type SettingsChoices,
  type SettingsRow,
  type SettingsRun,
  type SettingsSequence,
} from '@/features/document'
import { INSPECTOR_HOST } from '../../inspectorHost'
import type { InspectorTarget } from '../../inspectorTarget'
import {
  useInspectorEvents,
  useInspectorResource,
  type InspectorEventMatch,
  type InspectorEventScope,
  type InspectorResourceStatus,
} from '../../useInspectorResource'
import {
  settingsAvailability,
  settingsBlockedBy,
  settingsChanged,
  settingsDraftOf,
  settingsDraftWith,
  settingsErrorKey,
  settingsFailureKey,
  settingsHopView,
  settingsProviderOptions,
  settingsReviewCountOptions,
  settingsRowFor,
  settingsRowState,
  type SettingsAvailability,
  type SettingsBlocked,
  type SettingsDraft,
  type SettingsField,
  type SettingsHopView,
  type SettingsOption,
} from './settingsModel'

/** Server events after which the Flow's rows and run are read again. */
export const SETTINGS_EVENTS = [
  'group_view_refresh',
  'document_explorer_refresh',
  'doc_review_status_changed',
  'ai_invoke_started',
  'ai_invoke_finished',
] as const

/** An event of the target Flow (any of its documents, or the Flow itself). */
export const settingsEventMatches: InspectorEventMatch = (frame, scope) => frame.groupId === scope.flowId

/** The Flow's sequence with the facts that guard a save. */
export interface SettingsState {
  readonly sequence: SettingsSequence
  readonly run: SettingsRun
  readonly groupDisposed: boolean
}

/**
 * Reads the Flow's rows, then its run and its group's disposal. The rows decide the read; a guard read
 * that fails does not block (the server refuses on the same facts and the rows are read again after).
 */
export async function settingsStateRead(flowId: string): Promise<SettingsState> {
  const [sequence, run] = await Promise.all([settingsRead(flowId), settingsRunRead(flowId).catch(() => ({ active: false, hop: null }))])
  const groupDisposed = await reviewDocumentRead(sequence.rootDocId).then(
    (document) => document.groupDisposed,
    () => false,
  )
  return { sequence, run, groupDisposed }
}

// The resource key is the Flow id: the sequence is the Flow's, not a document's. A Node without a v0.1
// slot has no row, so nothing is read for it.
function scopeOf(target: InspectorTarget | null): InspectorEventScope | null {
  return target?.slot ? { projectId: target.projectId, flowId: target.flowId, docId: target.flowId } : null
}

function choicesScopeOf(target: InspectorTarget | null): InspectorEventScope | null {
  return target?.slot ? { projectId: target.projectId, flowId: target.flowId, docId: target.projectId } : null
}

export interface SettingsFailure {
  readonly key: string
}

export interface SettingsFieldView {
  readonly value: string
  readonly options: readonly SettingsOption[]
}

export interface SettingsTabState {
  /** The Node's row, or null (no slot, not read yet, or not in the read). */
  readonly row: ComputedRef<SettingsRow | null>
  /** The Node has no v0.1 sequence slot: there is nothing to set. */
  readonly noSlot: ComputedRef<boolean>
  readonly fields: ComputedRef<Readonly<Record<SettingsField, SettingsFieldView>> | null>
  readonly availability: ComputedRef<SettingsAvailability>
  /** What the running hop of this row took from it, or null. */
  readonly hop: ComputedRef<SettingsHopView | null>
  readonly status: Readonly<Ref<InspectorResourceStatus>>
  /** Message key of the failed read, or null. */
  readonly errorKey: ComputedRef<string | null>
  readonly busy: Readonly<Ref<boolean>>
  /** The last refused save of this Node, or null. */
  readonly failure: Readonly<Ref<SettingsFailure | null>>
  /** The last save of this Node went through (until the next change). */
  readonly saved: Readonly<Ref<boolean>>
  set(field: SettingsField, value: string): void
  /** Saves the Node's row; nothing is sent when saving is disabled or nothing changed. */
  save(): Promise<boolean>
  reload(): Promise<void>
}

export function useSettings(target: () => InspectorTarget | null): SettingsTabState {
  const host = inject(INSPECTOR_HOST, null)
  const scope = () => scopeOf(target())
  const state = useInspectorResource<SettingsState>({
    target: scope,
    read: settingsStateRead,
    events: SETTINGS_EVENTS,
    matches: settingsEventMatches,
  })
  const choices = useInspectorResource<SettingsChoices>({
    target: () => choicesScopeOf(target()),
    read: settingsChoicesRead,
    events: [],
  })

  const draft = ref<SettingsDraft | null>(null)
  const busy = ref(false)
  const failure = ref<SettingsFailure | null>(null)
  const saved = ref(false)
  const blocked = ref<SettingsBlocked>(null)

  // The Node keeps its node_key when a save writes its row again under a new id, so the key is the Node's.
  const nodeKey = () => {
    const current = target()
    return current ? `${current.flowId}\n${current.nodeId}` : ''
  }
  // A draft, a refusal and what it blocks belong to the Node they were made on.
  watch(nodeKey, () => {
    draft.value = null
    failure.value = null
    saved.value = false
    blocked.value = null
  })

  // A guard event may lift what a refusal blocked: let the read decide again.
  useInspectorEvents(scope, {
    events: SETTINGS_EVENTS,
    matches: settingsEventMatches,
    resync: false,
    changed: () => {
      blocked.value = null
    },
  })

  const readState = computed(() => state.data.value)

  const noSlot = computed(() => !target()?.slot)
  const row = computed(() => settingsRowFor(readState.value?.sequence.items ?? null, target()?.slot ?? null))
  const shown = computed<SettingsDraft | null>(() => draft.value ?? (row.value ? settingsDraftOf(row.value) : null))
  const changed = computed(() => (row.value ? settingsChanged(row.value, draft.value) : false))

  // The read holds no row for the slot: the rows were written again since the Canvas was read (another
  // save). Read the Canvas again once for that slot so the Node takes its row's new id.
  const asked = new Set<string>()
  const slotKey = (current: InspectorTarget, itemId: number) => `${current.flowId}\n${itemId}`
  watch(
    () => {
      const current = target()
      if (!current?.slot || !readState.value || row.value || busy.value) return null
      return slotKey(current, current.slot.itemId)
    },
    (key) => {
      if (key === null || asked.has(key)) return
      asked.add(key)
      host?.request({ kind: 'refresh-flow' })
    },
  )

  const availability = computed(() =>
    settingsAvailability({
      ownership: target()?.ownership ?? null,
      readOnly: target()?.readOnly ?? null,
      groupDisposed: readState.value?.groupDisposed === true,
      aiRunning: readState.value?.run.active === true,
      blocked: blocked.value,
      row: row.value ? settingsRowState(row.value) : null,
      reviewCount: shown.value?.reviewCount ?? 0,
      changed: changed.value,
      busy: busy.value,
    }),
  )

  const fields = computed(() => {
    const current = shown.value
    if (!current) return null
    const providers = choices.data.value?.providers ?? []
    const counts = choices.data.value?.reviewCounts ?? [-1, 0]
    return {
      provider: {
        value: current.providerId ?? '',
        options: settingsProviderOptions(providers, { id: current.providerId, name: current.providerDisplayName }),
      },
      reviewCount: { value: String(current.reviewCount), options: settingsReviewCountOptions(counts, current.reviewCount) },
      reviewer: {
        value: current.reviewerProviderId ?? '',
        options: settingsProviderOptions(providers, { id: current.reviewerProviderId, name: current.reviewerProviderDisplayName }),
      },
    }
  })

  const hop = computed(() => (row.value ? settingsHopView(row.value, readState.value?.run.hop ?? null) : null))

  const errorKey = computed(() => (state.status.value === 'error' ? settingsErrorKey(settingsErrorOf(state.error.value).kind) : null))

  function set(field: SettingsField, value: string): void {
    const current = shown.value
    if (!current || !availability.value[field].enabled) return
    draft.value = settingsDraftWith(current, field, value, choices.data.value?.providers ?? [])
    saved.value = false
    failure.value = null
  }

  async function save(): Promise<boolean> {
    const current = target()
    const values = draft.value
    const itemId = row.value?.itemId
    if (!current || !values || itemId === undefined || !availability.value.save.enabled) return false
    const key = nodeKey()
    busy.value = true
    failure.value = null
    saved.value = false
    let ok = false
    try {
      try {
        // The rows are read again right before the save: what goes back is what the server holds now.
        const fresh = await settingsRead(current.flowId)
        const freshRow = fresh.items.find((item) => item.itemId === itemId)
        if (!freshRow || settingsRowState(freshRow) !== 'editable') throw { response: { status: 409, data: { error: 'sequence_item_stale' } } }
        await settingsSave(fresh.rootDocId, fresh.items, { itemId, values })
        ok = true
      } catch (error) {
        if (nodeKey() === key) {
          const kind = settingsErrorOf(error).kind
          failure.value = { key: settingsFailureKey(kind) }
          blocked.value = settingsBlockedBy(kind) ?? blocked.value
          if (kind === 'stale') host?.request({ kind: 'refresh-flow' })
        }
      }
      if (ok && nodeKey() === key) {
        draft.value = null
        saved.value = true
        // The rows written again carry new ids: the Canvas reads them and keeps each slot's node_key.
        asked.add(slotKey(current, itemId))
        host?.request({ kind: 'refresh-flow' })
      }
      await state.reload()
    } finally {
      busy.value = false
    }
    return ok
  }

  return {
    row,
    noSlot,
    fields,
    availability,
    hop,
    status: state.status,
    errorKey,
    busy,
    failure,
    saved,
    set,
    save,
    reload: async () => {
      blocked.value = null
      await Promise.all([state.reload(), choices.reload()])
    },
  }
}
