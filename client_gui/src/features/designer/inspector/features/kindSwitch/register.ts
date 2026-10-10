// flowgate.v02.0048 T#10 — the R↔B kind switch (D#1 §6.3 order 610, header action): requirement / bug
// cards with a bound document on an unassigned Flow. v01, Graph and conflict Flows are refused by the
// server whatever the state (D#2 E1 / E2, §3.3), so the switch is not registered there (D#2 §3.4 /
// §3.5, D#1 §5.3 rule 6). Whether it is usable now is kindSwitchModel's.
// flowgate.v02.0051 stage 2: placed as the header kind badge (headerKind, the v7 종류 배지 ▾), not as
// a header action.
import type { InspectorRegistration } from '../../inspectorRegistry'
import KindSwitchAction from './KindSwitchAction.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'kindSwitch',
  order: 610,
  cardKinds: ['requirement'],
  docTypes: ['R', 'B'],
  ownerships: ['unassigned'],
  requiresDocument: true,
  headerKind: KindSwitchAction,
  messages,
}

export default registration
