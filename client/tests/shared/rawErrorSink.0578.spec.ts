import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * flowgate.default.0578 T0012 §2.4 / NR0003 §6 (client static check): a regression
 * guard for the raw-response sink patterns T0010/TR0011 (T#3) removed from these files --
 * `error.message`/`err.message`/`e.message` and `detail?.message` read directly into a
 * UI sink, `String(e)` used as a fallback that stringifies the raw exception, and
 * (T0012 §2.4 reopened finding) `response.data.detail` itself -- the bare object, not just
 * its `.message` sub-field -- poured into a display sink with no `.code`/`.errors` access
 * after it (`errorMessage.value = e?.response?.data?.detail`,
 * `showToast(err.response.data.detail)`). This is a text/regex scan (not an AST check) so
 * it stays cheap and does not need each file's import graph; the fixture tests below prove
 * it actually catches something before the real-file tests prove today's sources are clean.
 *
 * `detail.code` / `error.code` comparisons and the `sequence_changed`/`wp_changed` string
 * branches are NOT raw-message display -- they compare an identifier, they never put
 * server text on screen -- so the patterns below deliberately require a literal
 * `.message` (or `String(e)`, or a `detail` reference with no further property access),
 * never a bare `.code` or `===` comparison, and never a `detail` chain that continues on
 * into `.code`/`.errors`/etc.
 */

const CLIENT_ROOT = process.cwd()

// T0010 §1's 8 extractApiErrorMessage call-site files (7) + AiSettingsView.vue (T0012 §2.4
// minimum scan set). FileTreeNode.vue and WorkflowDecisionModal.vue are in this set on
// purpose -- they are exactly where the ALLOWED detail.code / sequence_changed / wp_changed
// branches live, so a false positive there would be caught immediately.
const TARGET_FILES = [
  'src/main/components/CreateFileFolderModal.vue',
  'src/main/components/FileTreeNode.vue',
  'src/main/components/NewRequirementModal.vue',
  'src/main/components/WorkflowDecisionModal.vue',
  'src/main/components/WorkPlanCreateDialog.vue',
  'src/main/components/WorkPlanProposalDialog.vue',
  'src/main/composables/useFileUpload.ts',
  'src/settings/views/system/AiSettingsView.vue',
]

// error/err/serverErr(...).message, optionally through .response/.data, optional-chained
// or not. Deliberately requires an error-ish base identifier so it does not fire on an
// unrelated `.message` (e.g. CreateFileFolderModal.vue's `data.message`, a server response
// FIELD that is shown verbatim by design, not an exception's `.message`).
const ERROR_MESSAGE_ACCESS = /\b(?:e|\w*err\w*)\??\.(?:response\??\.)?(?:data\??\.)?message\b/i
// `detail`/`detail?.message` read directly, distinct from the allowed `detail?.code` /
// `detail?.errors` structured reads.
const RAW_DETAIL_MESSAGE = /\bdetail\??\.message\b/i
// `response.data.detail` (or `response?.data?.detail`) itself poured into a sink, with
// nothing after `detail` -- no `.code`, no `.errors`, no further property access. The
// negative lookahead is what tells this apart from the allowed `detail?.code` /
// `detail?.errors` chains: `data?.detail?.code` has `?.code` right after `detail` and is
// not flagged; `data?.detail` used bare (assigned, passed as an argument, or ended by a
// line break/`)`/`||`) has nothing after it and IS flagged.
const RAW_DETAIL_BARE = /\bdata\??\.detail\b(?!\??\.\w)/i
// The exact fallback pattern TR0011 removed from WorkPlanCreateDialog.vue/WorkPlanProposalDialog.vue.
const STRINGIFIED_ERROR = /\bString\(\s*(?:e|err|error)\s*\)/i

function findRawSinkHits(source: string): string[] {
  const hits: string[] = []
  for (const line of source.split(/\r?\n/)) {
    if (
      ERROR_MESSAGE_ACCESS.test(line) ||
      RAW_DETAIL_MESSAGE.test(line) ||
      RAW_DETAIL_BARE.test(line) ||
      STRINGIFIED_ERROR.test(line)
    ) {
      hits.push(line.trim())
    }
  }
  return hits
}

describe('raw error-response sink regression guard (flowgate.default.0578 T0012 §2.4)', () => {
  it('catches the exact e?.response?.data?.message fallback TR0011 removed', () => {
    const fixture = [
      'try {',
      "  await postRequest('/api/v1/storage/folder', {})",
      '} catch (e: any) {',
      "  errorMessage.value = e?.response?.data?.message || t('main.create_file_folder_modal.error_save_failed')",
      '}',
    ].join('\n')
    expect(findRawSinkHits(fixture)).not.toEqual([])
  })

  it('catches the detail?.message || String(e) fallback TR0011 removed', () => {
    const fixture =
      "createError.value = extractApiErrorMessage(e, detail?.message || String(e))"
    expect(findRawSinkHits(fixture)).not.toEqual([])
  })

  it('catches response.data.detail assigned directly to a display sink (T0012 §2.4 reopened finding)', () => {
    const fixture = "errorMessage.value = e?.response?.data?.detail"
    expect(findRawSinkHits(fixture)).not.toEqual([])
  })

  it('catches response.data.detail passed directly into a toast/dialog call (T0012 §2.4 reopened finding)', () => {
    const fixture = "showToast(err.response.data.detail, 'error')"
    expect(findRawSinkHits(fixture)).not.toEqual([])
  })

  it('does not flag detail.code / error.code comparisons or sequence_changed / wp_changed branches', () => {
    const fixture = [
      "const code = e?.response?.data?.detail?.code",
      "switch (code) {",
      "  case 'FILE_NOT_DELETED': return t('main.file_tree_node.delete_invalid_path')",
      "}",
      "if (e?.response?.data?.error === 'sequence_changed') {",
      "  showToast(t('main.work_plan_pour.error_sequence_changed'), 'error')",
      "} else if (e?.response?.data?.error === 'wp_changed') {",
      "  showToast(t('main.work_plan_pour.error_wp_changed'), 'error')",
      "}",
      "const errors = e?.response?.data?.detail?.errors",
      "saveErrors.value = formatErrors(e.response.data?.detail?.errors, providers.value, { t, te })",
    ].join('\n')
    expect(findRawSinkHits(fixture)).toEqual([])
  })

  it.each(TARGET_FILES)('has no raw-response sink in %s', (relPath) => {
    const source = readFileSync(join(CLIENT_ROOT, relPath), 'utf-8')
    expect(findRawSinkHits(source)).toEqual([])
  })
})
