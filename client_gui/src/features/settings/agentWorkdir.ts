// flowgate.v02test.0043 CH0004: the Agent's job folder location. The person types only the parent
// folder; every Job gets its own folder under it, so the stored setting always carries
// ${AGENT_JOB_ID} (the server replaces it with the Job id: provider_remote.job_workdir).
export const AGENT_JOB_VAR = '${AGENT_JOB_ID}'

/** `<parent><separator>${AGENT_JOB_ID}` at the end of a stored value. */
const VAR_AT_END = /^(.*)([\\/])\$\{AGENT_JOB_ID\}$/
const DRIVE_ONLY = /^[A-Za-z]:$/

/** The folder separator of a typed path: Windows-looking paths get `\`. */
function separatorOf(path: string): '\\' | '/' {
  if (path.includes('\\')) return '\\'
  if (path.includes('/')) return '/'
  return DRIVE_ONLY.test(path) ? '\\' : '/'
}

/**
 * What the input shows for a stored value: the parent folder when the value ends with the variable,
 * otherwise the value as it is (a variable in the middle is a hand-written setting and stays visible).
 */
export function workdirParent(stored: string | null | undefined): string {
  if (!stored) return ''
  const m = VAR_AT_END.exec(stored)
  if (!m || m[1].includes(AGENT_JOB_VAR)) return stored
  return m[1] === '' || DRIVE_ONLY.test(m[1]) ? m[1] + m[2] : m[1]
}

/** The setting to store for what was typed: null when empty, the variable appended when missing. */
export function composeWorkdir(typed: string): string | null {
  const text = typed.trim()
  if (!text) return null
  if (text.includes(AGENT_JOB_VAR)) return text
  return `${text.replace(/[\\/]+$/, '')}${separatorOf(text)}${AGENT_JOB_VAR}`
}

/** A stored setting with the Job id shown as `job_…`, for the preview under the input. */
export function jobFolderPreview(stored: string): string {
  return stored.split(AGENT_JOB_VAR).join('job_…')
}
