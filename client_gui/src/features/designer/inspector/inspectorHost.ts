// flowgate.v02.0048 T#1 — Host intents (D#1 §5.7): what an Inspector feature may ask of the
// Designer outside the Inspector. The Frame provides the host; a feature composable injects it
// and calls `request`. The intent travels Frame -> Bottom Dock -> Center column (-> Shell), so no
// feature ever touches the Shell or the Center session itself. New intents are not added by a
// feature set (D#1 §5.7 last rule).
// flowgate.v02.0070 T#1 (B1): one way back with an answer — `saveTitle`, the card title save of the Inspector header,
// for the Flow and card the edit was opened on (not the selection at answer time). The Center column carries it to the
// session's saveNodeTitle; absent = titles are not saved here (no edit opens).
import type { InjectionKey, Ref } from 'vue'
import type { NodeTitleSaveOutcome } from '../center/graph/nodeTitle'
import type { InspectorTarget } from './inspectorTarget'

export type InspectorHostIntent =
  /** Select another Node of the same Flow ("→ 해당 카드"). */
  | { readonly kind: 'select-node'; readonly nodeId: string }
  /**
   * Read a Flow's Canvas again quietly (after a change that sends no event): the given open Flow, or the
   * selected (active) Flow when none is given. flowgate.v02.0048 T#10: an answer that comes after another
   * Flow tab was activated names the Flow it was asked for.
   */
  | { readonly kind: 'refresh-flow'; readonly flowId?: string }
  /** Open the right Tool Panel on the given tool. */
  | { readonly kind: 'show-tool'; readonly tool: 'git' }

export interface InspectorHost {
  request(intent: InspectorHostIntent): void
  /** Save a card's own title (the raw text; the server normalizes and decides). Absent = no title is saved. */
  readonly saveTitle?: (flowId: string, nodeKey: string, raw: string) => Promise<NodeTitleSaveOutcome>
}

export const INSPECTOR_HOST: InjectionKey<InspectorHost> = Symbol('flowgate.inspectorHost')

// flowgate.v02.0053 T#6 — the tab Content element: the Frame also provides the element that shows the
// active tab's Content (the scrolling body, role=tabpanel), so a part that takes input over its whole tab —
// the attachments file drop — listens there and not only on its own box: the free space under a short
// list and the body's padding belong to the tab as well. A part mounted outside the Frame gets none.
export interface InspectorBody {
  /** The tab Content element while tabs are shown, null before it is mounted. */
  readonly element: Readonly<Ref<HTMLElement | null>>
}

export const INSPECTOR_BODY: InjectionKey<InspectorBody> = Symbol('flowgate.inspectorBody')

// flowgate.v02test.0002 T-b (review rej_01M6R14HCES8QJJ5) - the selection: the Frame also provides the target it shows
// now, for a part whose state lives with the Inspector's selection of a card rather than with the part itself (a tab
// change unmounts the part while the card stays selected). Reactive; null while nothing is selected, the Designer has no
// work target, or the Frame is gone. A part mounted outside the Frame gets none.
export interface InspectorSelection {
  readonly target: InspectorTarget | null
}

export const INSPECTOR_SELECTION: InjectionKey<InspectorSelection> = Symbol('flowgate.inspectorSelection')
