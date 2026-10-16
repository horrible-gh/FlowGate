// Public surface of features/git (flowgate.v02.0033): the Designer right Tool Panel `Git` tab
// part a Tool Panel Container mounts —
// `<DesignerGitPanel :project-id :flow :active :read-only @badge-change @open-diff
//   @open-confirm-settings @windows-change />` — plus the data boundary a container or test may
// replace and the shapes of the part's events. Cards, windows, the session and the API adapter
// stay internal. flowgate.v02.0066 adds the Group worktree boundary type (GitWorktreeSource) and
// the worktree / group values of the `open-diff` intent (GitFileOrigin, GitDiffIntent).
export { default as DesignerGitPanel } from './DesignerGitPanel.vue'
export type { GitSource, GitWorktreeSource } from './gitSource'
export type {
  GitApplyRequest,
  GitBadge,
  GitConfirmSettingsIntent,
  GitConnectBusy,
  GitConnectForm,
  GitConnectInitial,
  GitConnectMessage,
  GitConnectProvider,
  GitConnectTestState,
  GitDiffIntent,
  GitFileLetter,
  GitFileOrigin,
  GitWindowDescriptor,
  GitWindowKind,
} from './types'
