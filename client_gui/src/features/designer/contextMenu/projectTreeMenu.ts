// flowgate.v02.0060 — ① project tree menu: project / group / Flow (0006-D rev2 §5.3 ①). Snapshot +
// availability + connected commands in, items and requests out. Flow 삭제 requests the existing
// dispose (DC) only; the part has no permanent-delete command.

import { action as a, makeMenu, separator as s } from './model'
import type { PartInput, ProjectTarget } from './types'
export interface ProjectAvailability { readonly currentProject: boolean; readonly disposed?: boolean }
export function projectTreeMenu({ target, available: v, connected }: PartInput<ProjectTarget, ProjectAvailability>) {
  const items = target.kind === 'project'
    ? [a('project.newGroup', 'newGroup', '＋', !v.currentProject), s('project-info'), a('project.info', 'projectInfo', 'ⓘ')]
    : target.kind === 'group'
      ? [a('group.newFlow', 'newFlow', '＋', !v.currentProject), s('group-info'), a('group.rename', 'rename', '✎', !v.currentProject), a('group.info', 'info', 'ⓘ')]
      : [a('flow.rename', 'rename', '✎', !!v.disposed), s('flow-delete'), a('flow.delete', 'delete', '×', !v.currentProject || !!v.disposed, true)]
  // flow.delete requests the existing DC workflow; it never performs permanent deletion.
  return makeMenu(target, items, connected)
}

