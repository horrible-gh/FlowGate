// flowgate.v02test.0077 — the Flow and card display ids on screen (tab `F77`, card head `#12`, Inspector `F77-12`).
//
// Display names and lookup aliases only: the group id, document ids and node keys stay the stored keys. A Flow number
// is the group number without its leading zeros, made here from the group id and never stored (the server's
// card_ids.flow_label, same rule); a card number comes from the server (`card_ref`, FlowCardRef). Pure: no Vue.
//
// To take the numbers off the screen later, drop the few places that render them (FlowTabStrip `flow-tab-no`,
// FlowNodeCard `flow-node-no`, InspectorHeader `inspector-header-card-id`); nothing else depends on them.

import type { FlowCardRef } from '@/features/project'

/**
 * A group number -> its Flow number: `"0077"` -> `F77`; null for a missing, non-numeric or zero number. The one client
 * rule — the Project Explorer (`flowLabelOf`) and the tabs / Inspector (`flowLabel`) both go through it.
 */
export function flowLabelOfNumber(number: string | null | undefined): string | null {
  if (!number || !/^\d{1,9}$/.test(number)) return null
  const value = Number(number)
  return value >= 1 ? `F${value}` : null
}

/** `flowgate.v02test.0077` -> `F77`; null for an id without a group number (a client-only Flow, malformed). */
export function flowLabel(groupId: string | null | undefined): string | null {
  const parts = (groupId ?? '').split('.')
  if (parts.length < 3 || parts.slice(0, -1).some((part) => !part)) return null
  return flowLabelOfNumber(parts[parts.length - 1])
}

/**
 * What ID 복사 copies for a card: its full display id (`flowgate.v02test.F77-12`); while it has no number (an older
 * server, a card not saved yet) its bound document id as before; null = nothing to copy.
 */
export function cardCopyId(cardRef: FlowCardRef | null | undefined, docId: string | null | undefined): string | null {
  return cardRef?.full || docId || null
}
