<template>
  <ModelessShell ref="shell" :open="open" :minimized="minimized" :initial-width="640" :initial-height="440" :min-width="360" :min-height="280" kind-color="#52b788" @update:minimized="minimized = $event" @close="emit('close')">
    <template #header>{{ words.title }}</template>
    <section class="station" data-test="git-station">
      <div class="station-row"><input v-model="search" type="search" :placeholder="words.find" :aria-label="words.find" /><span>{{ filtered.length }}/{{ branches.length }}</span><button :disabled="loading || busy" :title="words.refresh" :aria-label="words.refresh" @click="load">⟳</button></div>
      <p v-if="loading" role="status">{{ words.loading }}</p>
      <div v-else class="station-list" role="listbox" :aria-label="words.branches">
        <button v-for="branch in filtered" :key="branch.name" role="option" :aria-selected="selected === branch.name" :disabled="busy" :title="branch.name" @click="selected = branch.name">
          <span>{{ branch.name }}</span><small>{{ kindLabel(branch.kind) }}</small>
        </button>
      </div>
      <template v-if="picked">
        <div class="station-row"><b :title="picked.name">{{ picked.name }}</b><span v-if="picked.ahead !== null">↑ {{ picked.ahead }} · ↓ {{ picked.behind ?? '—' }}</span></div>
        <div v-if="!readOnly && picked.canBeSource" class="station-row"><input v-model="name" :disabled="busy" :placeholder="words.name" :aria-label="words.name" @keydown.enter="create" /><button :disabled="busy || !name.trim()" @click="create">{{ words.create }}</button></div>
        <button v-if="!readOnly && picked.kind === 'remote_only'" :disabled="busy" @click="importBranch">{{ words.import }}</button>
      </template>
      <p v-if="notice" role="status">{{ notice }}</p>
    </section>
  </ModelessShell>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '../../components/ui/ModelessShell.vue'
import { fetchStationBranches, createStationBranch, importStationBranch, gitErrorOf, type StationBranch } from './api'
const props = defineProps<{ open: boolean; projectId: string | null; readOnly: boolean }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const { locale } = useI18n()
const words = computed(() => locale.value.startsWith('ko')
  ? { title:'Git 스테이션', find:'브랜치 찾기', branches:'브랜치', refresh:'새로고침', loading:'불러오는 중', name:'새 브랜치 이름', create:'만들기', import:'로컬로 가져오기', failed:'처리하지 못했습니다. 다시 시도해 주세요.', denied:'권한이 없습니다.', done:'완료', base:'베이스', local:'로컬', remote:'원격', work:'작업 브랜치' }
  : { title:'Git station', find:'Find branches', branches:'Branches', refresh:'Refresh', loading:'Loading', name:'New branch name', create:'Create', import:'Import locally', failed:'Could not complete. Please retry.', denied:'Permission denied.', done:'Done', base:'Base', local:'Local', remote:'Remote', work:'Work branch' })
const minimized = ref(false)
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
function restore(): void { minimized.value = false; shell.value?.bringToFront() }
defineExpose({ restore })
const branches = ref<StationBranch[]>([])
const selected = ref('')
const search = ref('')
const name = ref('')
const notice = ref('')
const loading = ref(false)
const running = ref(new Set<string>())
const busy = computed(() => !!props.projectId && running.value.has(props.projectId))
let generation = 0
let disposed = false
const picked = computed(() => branches.value.find(row => row.name === selected.value))
const filtered = computed(() => branches.value.filter(row => row.name.toLowerCase().includes(search.value.toLowerCase()))
  .sort((a,b) => Number(b.name === selected.value) - Number(a.name === selected.value) || Number(b.kind === 'base') - Number(a.kind === 'base') || a.name.localeCompare(b.name)))
const kindLabel = (kind: string) => kind === 'base' ? words.value.base : kind === 'local' ? words.value.local : kind === 'remote_only' ? words.value.remote : words.value.work
const current = (at: number, project: string) => !disposed && props.open && generation === at && props.projectId === project
function failure(error: unknown) { return gitErrorOf(error).status === 403 ? words.value.denied : words.value.failed }
async function load() {
  const project = props.projectId
  if (!props.open || !project || busy.value) return
  const at = ++generation
  loading.value = true; notice.value = ''
  try {
    const rows = await fetchStationBranches(project)
    if (!current(at, project)) return
    branches.value = rows
    if (!rows.some(row => row.name === selected.value)) selected.value = rows.find(row => row.kind === 'base')?.name ?? rows[0]?.name ?? ''
  } catch (error) { if (current(at, project)) { branches.value = []; notice.value = failure(error) } }
  finally { if (current(at, project)) loading.value = false }
}
async function mutate(action: 'create' | 'import') {
  const project = props.projectId
  const row = picked.value
  if (!props.open || !project || !row || props.readOnly || busy.value || loading.value) return
  if (action === 'create' && (!row.canBeSource || !name.value.trim())) return
  if (action === 'import' && row.kind !== 'remote_only') return
  const at = generation
  running.value.add(project); notice.value = ''
  try {
    const next = action === 'create' ? name.value.trim() : row.name
    if (action === 'create') await createStationBranch(project, next, row.name)
    else await importStationBranch(project, row.name)
    if (!current(at, project)) return
    selected.value = next; name.value = ''; running.value.delete(project)
    const refreshed = load()
    const refreshGeneration = generation
    await refreshed
    if (current(refreshGeneration, project)) { notice.value = words.value.done; emit('changed') }
  } catch (error) { if (current(at, project)) notice.value = failure(error) }
  finally {
    const wasRunning = running.value.delete(project)
    if (wasRunning && !disposed && props.open && props.projectId === project && generation !== at) void load()
  }
}
const create = () => void mutate('create')
const importBranch = () => void mutate('import')
watch(() => [props.open, props.projectId], () => {
  generation++; branches.value = []; selected.value = ''; search.value = ''; name.value = ''; notice.value = ''; loading.value = false; minimized.value = false
  if (props.open) void load()
}, { immediate: true })
onBeforeUnmount(() => { disposed = true; generation++ })
</script>

<style scoped>
.station { display:flex; flex-direction:column; gap:10px; padding:12px; min-height:0; font-size:12px }
.station-row { display:flex; align-items:center; gap:8px; min-width:0 }.station-row b { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1 }
.station input { min-width:0; flex:1; padding:6px }.station input,.station button { background:#111e30; color:#dce8f5; border:1px solid #30445f; border-radius:5px; font:inherit }.station button { padding:5px 8px; cursor:pointer }.station button:disabled { opacity:.45; cursor:default }
.station-list { height:224px; overflow:auto; border:1px solid #30445f; border-radius:5px }.station-list button { display:flex; gap:8px; width:100%; height:28px; border:0; text-align:left; border-radius:0 }.station-list button[aria-selected=true] { background:#24466e }.station-list span { flex:1; overflow:hidden; white-space:nowrap; text-overflow:ellipsis }.station-list small { color:#9cb2ce }.station p { margin:0 }
</style>
