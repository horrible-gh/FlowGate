<template>
  <div class="evidence-files">
    <p v-if="failure" role="alert">{{ words.failed }}: {{ failure }}</p>
    <section v-for="kind in kinds" :key="kind" :data-test="`case-evidence-${kind}`" @dragover.prevent @drop.prevent="drop($event, kind)">
      <strong>{{ words[kind] }}</strong>
      <label class="add"><input type="file" multiple :disabled="busy || !ready" @change="choose($event, kind)" />{{ words.add }}</label>
      <p v-if="!currentFiles(kind).length">{{ words.empty }}</p>
      <div v-for="file in currentFiles(kind)" :key="file.key" class="file">
        <button type="button" @click="download(file)">{{ file.filename }} ↓</button>
        <img v-if="previews[file.key]?.kind === 'image'" :src="previews[file.key].src" :alt="file.filename" />
        <pre v-if="previews[file.key]?.kind === 'text'">{{ previews[file.key].text }}</pre>
      </div>
      <details v-if="oldFiles(kind).length">
        <summary>{{ words.previous }} ({{ oldFiles(kind).length }})</summary>
        <div v-for="file in oldFiles(kind)" :key="file.key" class="file">
          <button type="button" @click="download(file)">{{ file.test_evidence.attempt }} · {{ file.filename }} ↓</button>
          <img v-if="previews[file.key]?.kind === 'image'" :src="previews[file.key].src" :alt="file.filename" />
          <pre v-if="previews[file.key]?.kind === 'text'">{{ previews[file.key].text }}</pre>
        </div>
      </details>
    </section>
  </div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { testSpecEvidenceList, testSpecEvidenceUpload, testSpecEvidencePreview, testSpecEvidenceDownload, type TestSpecEvidenceFile as EvidenceFile, type TestSpecEvidencePreview as Preview } from '@/features/document'
type Kind = 'input' | 'log' | 'output'
const props = defineProps<{ tsId: string; caseId: string; revision?: unknown }>()
const emit = defineEmits<{ eligibility: [reason: string | null]; uploaded: [] }>()
const { locale } = useI18n()
const dictionary = {
  ko: { input: '입력 증거', log: '로그', output: '출력 증거', add: '+ 추가 / 끌어 놓기', empty: '첨부 파일 없음', previous: '이전 시도', failed: '파일 처리 실패' },
  en: { input: 'Input evidence', log: 'Logs', output: 'Output evidence', add: '+ Add / drop files', empty: 'No attachments', previous: 'Previous attempts', failed: 'File operation failed' },
  ja: { input: '入力証拠', log: 'ログ', output: '出力証拠', add: '+ 追加 / ドロップ', empty: '添付ファイルなし', previous: '以前の試行', failed: 'ファイル処理に失敗' },
}
const words = computed(() => dictionary[locale.value.split('-')[0] as keyof typeof dictionary] ?? dictionary.en)
const kinds: Kind[] = ['input', 'log', 'output']
const files = ref<EvidenceFile[]>([]), previews = ref<Record<string, Preview>>({})
const attempt = ref(1), ready = ref(false), busy = ref(false), failure = ref('')
let generation = 0
const urls = new Set<string>()
function clearUrls() { for (const url of urls) URL.revokeObjectURL(url); urls.clear() }
onBeforeUnmount(() => { generation++; clearUrls() })
const currentFiles = (kind: Kind) => files.value.filter(f => f.test_evidence.kind === kind && f.test_evidence.attempt === attempt.value)
const oldFiles = (kind: Kind) => files.value.filter(f => f.test_evidence.kind === kind && f.test_evidence.attempt < attempt.value)
async function refresh() {
  const version = ++generation
  clearUrls()
  ready.value = false; emit('eligibility', 'evidence_unavailable')
  files.value = []; previews.value = {}; failure.value = ''
  try {
    const data = await testSpecEvidenceList(props.tsId, props.caseId)
    if (version !== generation) return
    files.value = data.attachments
    const state = data.cases[props.caseId]
    if (!state) throw new Error('case_not_found')
    attempt.value = state.attempt; ready.value = true; emit('eligibility', state.pass_reason)
    await Promise.all(data.attachments.map(async file => {
      try {
        const result = await testSpecEvidencePreview(props.tsId, file.key)
        if (result.kind === 'image' && result.download) {
          const blob = await testSpecEvidenceDownload(props.tsId, file.key)
          if (version !== generation) return
          result.src = URL.createObjectURL(blob); urls.add(result.src)
        }
        if (version === generation) previews.value[file.key] = result
      } catch { /* Download remains available when a preview cannot be read. */ }
    }))
  } catch (error) { if (version === generation) failure.value = String(error) }
}
watch(() => [props.tsId, props.caseId, props.revision], () => { void refresh() }, { immediate: true })
async function upload(items: File[], kind: Kind) {
  if (busy.value || !ready.value || !items.length) return
  busy.value = true; failure.value = ''
  const version = generation, tsId = props.tsId, caseId = props.caseId, at = attempt.value
  try {
    for (const file of items) {
      if (version !== generation) break
      await testSpecEvidenceUpload(tsId, caseId, file, kind, at)
    }
    if (version === generation) { await refresh(); emit('uploaded') }
  } catch (error) { if (version === generation) { await refresh(); failure.value = String(error); emit('uploaded') } }
  finally { busy.value = false }
}
function choose(event: Event, kind: Kind) { const input = event.target as HTMLInputElement; void upload(Array.from(input.files ?? []), kind); input.value = '' }
function drop(event: DragEvent, kind: Kind) { void upload(Array.from(event.dataTransfer?.files ?? []), kind) }
async function download(file: EvidenceFile) {
  try {
    const blob = await testSpecEvidenceDownload(props.tsId, file.key)
    const url = URL.createObjectURL(blob), anchor = document.createElement('a')
    anchor.href = url; anchor.download = file.filename; document.body.appendChild(anchor); anchor.click(); anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) { failure.value = String(error) }
}
</script>
<style scoped>
.evidence-files { display: grid; gap: 10px; }
section { border: 1px dashed #506277; border-radius: 5px; padding: 8px; }
.add { display: block; cursor: pointer; margin: 6px 0; }
.add input { max-width: 100%; }
.file { margin: 6px 0; }
.file button { color: inherit; background: transparent; border: 0; cursor: pointer; text-decoration: underline; }
img { display: block; max-width: 200px; max-height: 120px; object-fit: contain; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 180px; overflow: auto; }
</style>
