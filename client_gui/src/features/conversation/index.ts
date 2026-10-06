// Public surface of features/conversation (flowgate.v02.0031): the Conversation part a
// Designer Tool Panel Container mounts in its body —
// `<ConversationPanel :project-id :group-id @open-change />` — plus the data boundary a
// container or test may replace and the activity shapes it may push. Header / timeline /
// composer parts, the session and the API adapter stay internal.
export { default as ConversationPanel } from './ConversationPanel.vue'
export type { ConversationSource } from './conversationSource'
export type {
  ActivityAnchor,
  ChatSummary,
  CommandDecision,
  CommandRequestActivity,
  CommandState,
  CommandStatus,
  ConversationActivity,
  StatusActivity,
  WorkChangeActivity,
  WorkChangeFile,
} from './types'
