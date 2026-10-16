// Public surface of features/git (flowgate.v02.0033): the Designer right Tool Panel `Git` tab
// part a Tool Panel Container mounts —
// `<DesignerGitPanel :project-id :flow :active :read-only @badge-change @open-diff
//   @open-confirm-settings @windows-change />` — plus the data boundary a container or test may
// replace and the shapes of the part's events. Cards, windows, the session and the API adapter
// stay internal. flowgate.v02.0066 adds the Group worktree boundary type (GitWorktreeSource) and
// the worktree / group values of the `open-diff` intent (GitFileOrigin, GitDiffIntent). Its T#6
// adds the Designer Center Diff the Shell owns: the session factory, its screen and their types
// (0066 D#2 §2-3), and the `git-center-diff-refresh` request shape.
// flowgate.v02.0067 T#4 takes the Center Diff back inside the part — it is the Git modeless `Diff`
// tab detail the panel owns — so neither it nor `git-center-diff-refresh` is public any more; the
// worktree intents of `open-diff` no longer go up (the 0033 ones do, unchanged).
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
