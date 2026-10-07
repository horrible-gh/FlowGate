<template>
  <div class="fg-header" data-test="designer-header">
    <div class="fg-brand"><span class="fg-logo">FG</span><strong>FlowGate <em>Designer</em></strong>
      <button type="button" aria-label="알림" title="알림" @click="emit('command', 'notifications')">♧</button>
      <button type="button" class="fg-ai" @click="emit('command', 'ai')">✦ AI</button>
    </div>
    <div ref="searchRoot" class="fg-search">
      <span aria-hidden="true">⌕</span>
      <button type="button" class="fg-scope-chip" data-test="designer-search-scope" :aria-expanded="searchOpen" @click="searchOpen = !searchOpen">{{ scope }} ▾</button>
      <input ref="searchInput" v-model="query" data-test="designer-search-input" type="search" autocomplete="off" :placeholder="placeholder" @focus="searchOpen = true" @keydown.escape="searchOpen = false" @keydown.enter="emit('search', { scope, query })" />
      <kbd>Ctrl K</kbd>
      <div v-if="searchOpen" class="fg-search-pop" data-test="designer-search-assist">
        <div class="fg-pop-title">검색 범위 · 현재 Project 전체 대상</div>
        <div class="fg-scope-list">
          <button v-for="item in scopes" :key="item" type="button" :aria-pressed="scope === item" @click="scope = item; searchInput?.focus()">{{ item }}</button>
        </div>
        <div class="fg-pop-title">검색 예시</div>
        <div class="fg-examples">
          <button v-for="example in examples" :key="example.value" type="button" @click="query = example.value; searchInput?.focus()">{{ example.label }}</button>
        </div>
        <small>범위를 고른 뒤 입력하거나 예시를 눌러 검색어를 넣으세요.</small>
      </div>
    </div>
    <div class="fg-account">
      <div class="fg-languages" aria-label="언어 선택">
        <button v-for="code in ['ko', 'en', 'ja']" :key="code" type="button" :aria-pressed="locale === code" @click="locale = code">{{ code.toUpperCase() }}</button>
      </div>
      <button type="button" @click="emit('command', 'settings')">⚙ 설정</button>
      <button type="button" @click="emit('command', 'user')">● 사용자 ▾</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const emit = defineEmits<{ command: [id: string]; search: [payload: { scope: string; query: string }] }>()
const { locale } = useI18n()
const scopes = ['전체', '플로우', '노드', '파일', '문서']
const examples = [
  { label: 'Designer', value: 'Designer' },
  { label: '문서 ID · 0675', value: '0675' },
  { label: '파일 · workflow.py', value: 'workflow.py' },
  { label: '플로우 · 인증 오류 조사', value: '인증 오류 조사' },
]
const scope = ref('전체')
const query = ref('')
const searchOpen = ref(false)
const searchRoot = ref<HTMLElement | null>(null)
const searchInput = ref<HTMLInputElement | null>(null)
const placeholder = computed(() => ({ 전체: 'Project 전체에서 검색...', 플로우: '플로우 이름으로 검색...', 노드: '노드 이름 또는 ID로 검색...', 파일: '파일명으로 검색...', 문서: '문서 ID로 검색...' })[scope.value] ?? '검색어...')
function onDocumentPointer(event: PointerEvent) {
  if (searchRoot.value && !searchRoot.value.contains(event.target as Node)) searchOpen.value = false
}
function onKey(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    searchInput.value?.focus()
  }
}
onMounted(() => { document.addEventListener('pointerdown', onDocumentPointer); window.addEventListener('keydown', onKey) })
onUnmounted(() => { document.removeEventListener('pointerdown', onDocumentPointer); window.removeEventListener('keydown', onKey) })
</script>

<style scoped>
.fg-header{height:52px;display:grid;grid-template-columns:minmax(300px,1fr) minmax(260px,680px) minmax(250px,1fr);align-items:center;gap:14px;padding:0 16px;background:#0a1729;border-bottom:1px solid var(--fg2-line-strong);color:#edf5ff}
.fg-brand,.fg-account,.fg-languages,.fg-search,.fg-scope-list,.fg-examples{display:flex;align-items:center;gap:7px}.fg-brand{white-space:nowrap}.fg-brand strong{font-size:17px}.fg-brand em{font-style:normal;color:#65a4ff}.fg-logo{display:grid;place-items:center;width:34px;height:34px;border-radius:7px;background:#347cf5;font-weight:800}.fg-brand button:first-of-type{margin-left:20px}.fg-ai{color:#adc8ff!important;border-color:#356eff!important}.fg-account{justify-content:flex-end;white-space:nowrap}
button{border:1px solid #315071;border-radius:6px;background:#10213a;color:#d6e3f1;padding:6px 9px;cursor:pointer}button:hover,button:focus-visible{background:#19365a}.fg-languages{gap:0}.fg-languages button{border-radius:0;font-size:11px}.fg-languages button:first-child{border-radius:5px 0 0 5px}.fg-languages button:last-child{border-radius:0 5px 5px 0}.fg-languages button[aria-pressed=true],.fg-scope-list button[aria-pressed=true]{background:#245395;color:white}
.fg-search{height:34px;position:relative;border:1px solid #315071;border-radius:7px;background:#0d1d31;padding:0 8px;min-width:0}.fg-scope-chip{font-size:11px;border-radius:999px;padding:3px 7px;white-space:nowrap}.fg-search input{min-width:0;flex:1;border:0;outline:0;background:transparent;color:#edf5ff;font-size:12px}.fg-search kbd{white-space:nowrap;font-size:10px;color:#9fb0c6}.fg-search-pop{position:absolute;top:39px;left:0;right:0;z-index:60;background:#0c1c2e;border:1px solid #31577f;border-radius:8px;padding:10px;box-shadow:0 14px 36px #0009}.fg-pop-title{font-size:11px;color:#9bb2cb;margin:4px 0 7px}.fg-scope-list,.fg-examples{flex-wrap:wrap;margin-bottom:9px}.fg-scope-list button,.fg-examples button{font-size:11px}.fg-search-pop small{color:#91a8bf}
@media(max-width:1120px){.fg-header{grid-template-columns:auto minmax(190px,1fr) auto;padding:0 8px;gap:8px}.fg-brand strong{font-size:13px}.fg-account button{font-size:11px}.fg-languages{display:none}}
</style>
