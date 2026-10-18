// flowgate.v02.0060 — ② file explorer menu: folder (root = the explorer's blank area) / file
// (0006-D rev2 §5.3 ②). The root folder has no open / rename / delete. Git link state comes from a
// real status read, and read failure / no worktree / no permission stay distinct from "not linked".
// Git › 되돌리기 is judged only from group-HEAD facts (uncommitted change, tracked in HEAD); the
// Base-relative change badge is never used for it.

import { action as a, makeMenu, separator as s, submenu } from './model'
import type { DisabledReason, FileTarget, MenuAction, MenuItem, PartInput } from './types'
export interface FileAvailability {
  readonly expanded?: boolean
  readonly canMutate: boolean
  readonly virtualDeleted: boolean
  readonly canRead: boolean
  readonly git: 'linked' | DisabledReason
  /** Base diff is ONLY for changes, never evidence for a HEAD revert. */
  readonly hasChanges?: boolean
  readonly hasHeadChanges?: boolean
  readonly trackedInHead?: boolean
}
export function fileExplorerMenu({ target, available: v, connected }: PartInput<FileTarget, FileAvailability>) {
  const kind = target.kind
  const writable = v.canMutate && !v.virtualDeleted
  const gitDisabled = v.git !== 'linked'
  const gitChildren: MenuAction[] = [
    a(`${kind}.git.changes`, 'changes', '≋', gitDisabled || (kind === 'file' && !v.hasChanges)),
    a(`${kind}.git.revert`, 'revert', '↶', gitDisabled || !v.canMutate || !v.hasHeadChanges || (kind === 'file' && !v.trackedInHead), true),
    a(`${kind}.git.history`, 'history', '◷', gitDisabled),
  ].map((item) => ({ ...item, disabledReason: gitDisabled ? v.git as DisabledReason : undefined }))
  const git = { ...submenu(`${kind}.git`, 'git', '⑂', gitChildren), disabled: gitDisabled, disabledReason: gitDisabled ? v.git as DisabledReason : undefined }
  const items: MenuItem[] = kind === 'folder' ? [
    ...(!target.root ? [a('folder.open', 'open', '↗', !!v.expanded), s('folder-create')] : []),
    a('folder.newFolder', 'newFolder', '＋', !writable), a('folder.newFile', 'newFile', '＋', !writable),
    s('folder-transfer'), ...(!target.root ? [a('folder.rename', 'rename', '✎')] : []),
    a('folder.download', 'download', '↓', !v.canRead), a('folder.upload', 'upload', '↑', !writable), git,
    ...(!target.root ? [s('folder-delete'), a('folder.delete', 'delete', '×', !writable, true)] : []),
  ] : [
    a('file.open', 'open', '↗'), s('file-transfer'), a('file.rename', 'rename', '✎'),
    a('file.download', 'download', '↓', !v.canRead || v.virtualDeleted), git,
    s('file-delete'), a('file.delete', 'delete', '×', !writable, true),
  ]
  return makeMenu(target, items, connected)
}

