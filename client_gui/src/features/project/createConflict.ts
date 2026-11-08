// flowgate.v02.0152: the typed refusal of POST /api/v1/projects when the name or id is already
// taken. Kept out of api.ts so screens can test for it with `instanceof` even where api.ts itself
// is mocked.
import { normalizeApiError } from '@shared/apiErrors'

/**
 * flowgate.v02.0152: a create refused because the name or id is already taken. The Designer's
 * list holds active projects only, so an archived holder is invisible there; the dialog needs the
 * code and the holder's id/name to say which project it is (and that it can be restored) in the
 * person's own language, instead of the generic failure line.
 */
export type ProjectCreateConflictCode =
  | 'project_archived_conflict'
  | 'project_name_conflict'
  | 'project_id_conflict'

const PROJECT_CREATE_CONFLICT_CODES: readonly string[] = [
  'project_archived_conflict',
  'project_name_conflict',
  'project_id_conflict',
]

export class ProjectCreateConflictError extends Error {
  readonly code: ProjectCreateConflictCode
  readonly field: 'project_name' | 'project_id'
  readonly projectId: string
  readonly projectName: string

  constructor(
    code: ProjectCreateConflictCode,
    field: 'project_name' | 'project_id',
    projectId: string,
    projectName: string,
    message: string,
  ) {
    super(message)
    this.name = 'ProjectCreateConflictError'
    this.code = code
    this.field = field
    this.projectId = projectId
    this.projectName = projectName
  }
}

export function projectCreateConflictOf(error: unknown): ProjectCreateConflictError | null {
  const normalized = normalizeApiError(error)
  if (!normalized.code || !PROJECT_CREATE_CONFLICT_CODES.includes(normalized.code)) return null
  const source = normalized.source
  const field = source.field === 'project_id' ? 'project_id' : 'project_name'
  const text = (value: unknown): string => (typeof value === 'string' ? value : '')
  return new ProjectCreateConflictError(
    normalized.code as ProjectCreateConflictCode,
    field,
    text(source.project_id),
    text(source.project_name),
    text(source.message),
  )
}
