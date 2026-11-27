// F81 case window v8: one case's evidence files, shared by the three summary rows and the evidence windows.
// Listing, upload, attempt and pass eligibility work as TestEvidenceFiles did; the list is read once per
// case/revision, so 20 or 200 files cost the same one request. Previews are read only for a kind whose
// evidence window is open.
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  testSpecEvidenceDownload,
  testSpecEvidenceList,
  testSpecEvidencePreview,
  testSpecEvidenceUpload,
  type TestEvidenceKind,
  type TestSpecEvidenceFile,
  type TestSpecEvidencePreview,
  testSpecEvidenceDelete,
} from '@/features/document'

export const testEvidenceKinds: readonly TestEvidenceKind[] = ['input', 'log', 'output']
export interface TestEvidenceSource { tsId: string; caseId: string; flowId: string; revision: unknown }
export interface TestEvidenceHooks {
  /** The case's pass eligibility (`pass_reason`), `evidence_unavailable` while it cannot be read. */
  eligibility: (reason: string | null) => void
  /** A file was added or removed: the card is read again (result, counts, report). */
  changed: () => void
}

export function useTestEvidence(source: () => TestEvidenceSource | null, hooks: TestEvidenceHooks) {
  const files = ref<TestSpecEvidenceFile[]>([])
  const previews = reactive<Record<string, TestSpecEvidencePreview>>({})
  const attempt = ref(1), ready = ref(false), busy = ref(false), failure = ref('')
  const previewKinds = new Set<TestEvidenceKind>()
  let generation = 0
  /** Which case the files shown belong to: another case's files never stay on screen while its own list is read. */
  const sourceKey = (at: TestEvidenceSource) => `${at.tsId}\n${at.caseId}`
  let listedFor: string | null = null
  /** The call's source, only while the files shown are that source's (a stale key is never paired with a new case). */
  function listedSource() { const at = source(); return at && sourceKey(at) === listedFor ? at : null }
  const urls = new Set<string>()
  function clearUrls() { for (const url of urls) URL.revokeObjectURL(url); urls.clear() }
  onBeforeUnmount(() => { generation++; clearUrls() })

  /** Every file of a kind, newest first (the listing is in upload order). */
  const byKind = computed(() => Object.fromEntries(testEvidenceKinds.map(kind =>
    [kind, files.value.filter(file => file.test_evidence.kind === kind).reverse()])) as Record<TestEvidenceKind, TestSpecEvidenceFile[]>)
  const previous = (file: TestSpecEvidenceFile) => file.test_evidence.attempt < attempt.value

  async function refresh() {
    const at = source(), version = ++generation
    clearUrls()
    for (const key of Object.keys(previews)) delete previews[key]
    ready.value = false; failure.value = ''; hooks.eligibility('evidence_unavailable')
    // Another case (or none): its rows, [전체 N] and windows are emptied before the new list is read. The same case
    // read again (after an upload, a delete or a reload) keeps its list on screen, with add and delete off until read.
    if (!at || sourceKey(at) !== listedFor) { files.value = []; listedFor = null }
    if (!at) return
    try {
      const data = await testSpecEvidenceList(at.tsId, at.caseId)
      if (version !== generation) return
      files.value = data.attachments; listedFor = sourceKey(at)
      const state = data.cases[at.caseId]
      if (!state) throw new Error('case_not_found')
      attempt.value = state.attempt; ready.value = true; hooks.eligibility(state.pass_reason)
      for (const kind of previewKinds) void loadPreviews(kind)
    } catch (error) { if (version === generation) { files.value = []; listedFor = null; failure.value = String(error) } }
  }
  watch(() => { const at = source(); return at ? [at.tsId, at.caseId, at.revision] : null }, () => { void refresh() }, { immediate: true })

  /** Image thumbnails and the first lines of a text file, for the kind shown in an evidence window. */
  async function loadPreviews(kind: TestEvidenceKind) {
    previewKinds.add(kind)
    const at = listedSource(), version = generation
    if (!at) return
    await Promise.all(byKind.value[kind].filter(file => !previews[file.key]).map(async (file) => {
      try {
        const result = await testSpecEvidencePreview(at.tsId, file.key)
        if (result.kind === 'image' && result.download) {
          const blob = await testSpecEvidenceDownload(at.tsId, file.key)
          if (version !== generation) return
          result.src = URL.createObjectURL(blob); urls.add(result.src)
        }
        if (version === generation) previews[file.key] = result
      } catch { /* Download remains available when a preview cannot be read. */ }
    }))
  }
  function forgetPreviews(kind: TestEvidenceKind) { previewKinds.delete(kind) }

  async function upload(items: File[], kind: TestEvidenceKind) {
    const at = source()
    if (!at || busy.value || !ready.value || !items.length) return
    busy.value = true; failure.value = ''
    const version = generation, tries = attempt.value
    try {
      for (const file of items) {
        if (version !== generation) break
        await testSpecEvidenceUpload(at.tsId, at.caseId, file, kind, tries)
      }
      if (version === generation) { await refresh(); hooks.changed() }
    } catch (error) { if (version === generation) { await refresh(); failure.value = String(error); hooks.changed() } }
    finally { busy.value = false }
  }

  /**
   * One evidence file removed through the Flow attachment delete (testSpecEvidenceDelete); no evidence-only
   * server route is needed.
   */
  async function remove(file: TestSpecEvidenceFile) {
    const at = listedSource()
    if (!at || busy.value || !ready.value) return
    busy.value = true; failure.value = ''
    const version = generation
    try {
      await testSpecEvidenceDelete(at.flowId, file.key)
      if (version === generation) { await refresh(); hooks.changed() }
    } catch (error) { if (version === generation) { await refresh(); failure.value = String(error); hooks.changed() } }
    finally { busy.value = false }
  }

  async function download(file: TestSpecEvidenceFile) {
    const at = listedSource()
    if (!at) return
    try {
      const blob = await testSpecEvidenceDownload(at.tsId, file.key)
      const url = URL.createObjectURL(blob), anchor = document.createElement('a')
      anchor.href = url; anchor.download = file.filename; document.body.appendChild(anchor); anchor.click(); anchor.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) { failure.value = String(error) }
  }

  return { files, byKind, previews, attempt, ready, busy, failure, previous, refresh, upload, remove, download, loadPreviews, forgetPreviews }
}
export type TestEvidence = ReturnType<typeof useTestEvidence>
