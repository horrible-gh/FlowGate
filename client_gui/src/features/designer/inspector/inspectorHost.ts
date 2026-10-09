// flowgate.v02.0048 T#1 — Host intents (D#1 §5.7): what an Inspector feature may ask of the
// Designer outside the Inspector. The Frame provides the host; a feature composable injects it
// and calls `request`. The intent travels Frame -> Bottom Dock -> Center column (-> Shell), so no
// feature ever touches the Shell or the Center session itself. New intents are not added by a
// feature set (D#1 §5.7 last rule).
import type { InjectionKey } from 'vue'

export type InspectorHostIntent =
  /** Select another Node of the same Flow ("→ 해당 카드"). */
  | { readonly kind: 'select-node'; readonly nodeId: string }
  /** Read the selected Flow's Canvas again quietly (after a change that sends no event). */
  | { readonly kind: 'refresh-flow' }
  /** Open the right Tool Panel on the given tool. */
  | { readonly kind: 'show-tool'; readonly tool: 'git' }

export interface InspectorHost {
  request(intent: InspectorHostIntent): void
}

export const INSPECTOR_HOST: InjectionKey<InspectorHost> = Symbol('flowgate.inspectorHost')
