// flowgate.v02.0060 — ① project tree menu: project / group / Flow (0006-D rev2 §5.3 ①). Snapshot +
// availability + connected commands in, items and requests out. Flow Delete requests the existing
// dispose (DC) only; the part has no permanent-delete command.
// flowgate.v02gui.0027: the Group menu ends with Archive Group (Restore Group for an archived Group) — a display-only archive
// of the Group, independent of the Flow archive. Shown only where the host connects it; never offered for the
// "All" bucket of Flows without a Group (`groupArchivable: false`).
// flowgate.v02gui.0027 (0008-T): then Delete Group (danger), a single line — the item name only, no hint line under it
// (v02test.0027 0005-T removed the Flow-count / deletable·불가 explanation). It stays enabled whatever the Flow count: a Group
// with Flows opens the refusal window (with Archive instead), and the server's answer is always the final word. Never for the
// "All" bucket.

import { action as a, makeMenu, separator as s } from './model'
import type { PartInput, ProjectTarget } from './types'
export interface ProjectAvailability { readonly currentProject: boolean; readonly disposed?: boolean; readonly gitEnabled?: boolean; readonly archived?: boolean; readonly archiveEligible?: boolean; readonly archiveBusy?: boolean; readonly groupArchived?: boolean; readonly groupArchivable?: boolean; readonly groupArchiveBusy?: boolean }
export function projectTreeMenu({ target, available: v, connected }: PartInput<ProjectTarget, ProjectAvailability>) {
  const groupArchive = v.groupArchivable === false ? [] : [s('group-archive'), a(v.groupArchived ? 'group.restore' : 'group.archive', v.groupArchived ? 'groupRestore' : 'groupArchive', '▣', !v.currentProject || !!v.groupArchiveBusy)]
  const groupDelete = v.groupArchivable === false ? []
    : [s('group-delete'), a('group.delete', 'groupDelete', '×', !v.currentProject, true)]
  const items = target.kind === 'project'
    ? [a('project.newGroup', 'newGroup', '＋', !v.currentProject), s('project-info'), a('project.info', 'projectInfo', 'ⓘ')]
    : target.kind === 'group'
      ? [a('group.newFlow', 'newFlow', '＋', !v.currentProject), s('group-info'), a('group.rename', 'rename', '✎', !v.currentProject), a('group.info', 'info', 'ⓘ'), ...groupArchive, ...groupDelete]
      : [a('flow.rename', 'rename', '✎', !!v.disposed || !!v.archived), ...(v.gitEnabled === false ? [] : [a('flow.baseBranch', 'baseBranch', '⑂', !!v.archived)]), s('flow-archive'), a(v.archived ? 'flow.restore' : 'flow.archive', v.archived ? 'restore' : 'archive', '▣', !v.currentProject || !!v.archiveBusy || (!v.archived && !v.archiveEligible)), s('flow-delete'), a('flow.delete', 'delete', '×', !v.currentProject || !!v.disposed || !!v.archived, true)]
  // flow.delete requests the existing DC workflow; it never performs permanent deletion.
  return makeMenu(target, items, connected)
}

