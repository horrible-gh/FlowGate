// Public surface of features/conversation (flowgate.v02.0031): the Conversation part a
// Designer Tool Panel Container mounts in its body —
// `<ConversationPanel :project-id :group-id @open-change />` — plus the data boundary a
// container or test may replace and the activity shapes it may push. Header / timeline /
// composer parts, the session and the API adapter stay internal.
export { default as ConversationPanel } from './ConversationPanel.vue'
export type { ConversationSource } from './conversationSource'
// flowgate.v02.0066 — the pure old/new line comparison, shared with the file diff of the Git tab
// (0066 D#2 §2-3; the Git modeless `Diff` tab since 0067 T#4). Vue- and server-free; unchanged.
export { diffLines } from './changeDiff'
export type { DiffLine } from './changeDiff'
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
