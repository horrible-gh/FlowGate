// v02test.0096 F96-3: the sub-items of the 방침 tab, listed on its left in this order. Only 질의 exists today; a later
// policy layer adds an entry here (key, message key, panel) and the tab lists and opens it with no other change.
import { markRaw, type Component } from 'vue'
import QuestionPolicyPanel from './QuestionPolicyPanel.vue'

export interface PolicySection {
  key: string
  /** Message key of the name shown in the list. */
  label: string
  component: Component
}

export const POLICY_SECTIONS: readonly PolicySection[] = [
  { key: 'question', label: 'gui.aiManage.policy.question', component: markRaw(QuestionPolicyPanel) },
]
