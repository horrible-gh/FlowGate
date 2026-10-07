<template>
  <nav ref="root" class="fg-menubar" role="menubar" aria-label="Designer 메뉴" data-test="designer-menubar" @keydown="onKey">
    <div v-for="(group, index) in groups" :key="group.id" class="fg-menu-wrap" @mouseenter="onHover(index)">
      <button type="button" class="fg-menu-trigger" role="menuitem" :aria-haspopup="true" :aria-expanded="openIndex === index" :data-test="`designer-menu-${group.id}`" @click="toggle(index)">{{ group.label }}</button>
      <div v-if="openIndex === index" class="fg-menu-pop" role="menu">
        <template v-for="(item, itemIndex) in group.items" :key="`${group.id}-${itemIndex}`">
          <div v-if="item.kind === 'separator'" class="fg-menu-separator" role="separator" />
          <div v-else-if="item.kind === 'submenu'" class="fg-sub-wrap" @mouseenter="openSub = item.id" @mouseleave="openSub = null">
            <button type="button" class="fg-menu-item" role="menuitem" :aria-expanded="openSub === item.id" :aria-haspopup="true" :disabled="disabled(item)" @click="openSub = openSub === item.id ? null : item.id">{{ item.label }} <span>›</span></button>
            <div v-if="openSub === item.id" class="fg-sub-pop" role="menu">
              <button v-for="child in item.children" :key="child.id" type="button" class="fg-menu-item" role="menuitemradio" :aria-checked="child.id === `density.${density}`" @click="select(child.id)"><span class="fg-mark">{{ child.id === `density.${density}` ? '✓' : '' }}</span>{{ child.label }}</button>
              <div v-if="!item.children?.length" class="fg-empty">최근 항목 없음</div>
            </div>
          </div>
          <button v-else type="button" class="fg-menu-item" :role="item.kind === 'check' ? 'menuitemcheckbox' : item.kind === 'tab' ? 'menuitemradio' : 'menuitem'" :aria-checked="item.kind === 'check' ? checked(item.id) : item.kind === 'tab' ? activeTab(item.id) : undefined" :disabled="disabled(item)" @click="select(item.id)">
            <span v-if="item.kind === 'check' || item.kind === 'tab'" class="fg-mark">{{ item.kind === 'check' ? checked(item.id) ? '✓' : '' : activeTab(item.id) ? '●' : '' }}</span>{{ item.label }}
          </button>
        </template>
      </div>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import type { DesignerLeftMode, DesignerRightTool } from './designerLayout'

type Item = { id: string; label: string; kind?: 'separator' | 'submenu' | 'check' | 'tab'; children?: Item[]; requires?: 'project' | 'flow' | 'selection' | 'running' }
type Group = { id: string; label: string; items: Item[] }
const props = defineProps<{
  projectAvailable: boolean; flowAvailable: boolean; hasSelection?: boolean; running: boolean
  leftCollapsed: boolean; rightCollapsed: boolean; leftMode: DesignerLeftMode; rightTool: DesignerRightTool
  density: string
}>()
const emit = defineEmits<{ command: [id: string] }>()
const groups: Group[] = [
  { id: 'file', label: '파일', items: [
    { id: 'project.new', label: '새 Project' }, { id: 'project.open', label: 'Project 열기…' }, { id: 'project.recent', label: '최근 Project', kind: 'submenu', children: [] }, { id: 'project.close', label: 'Project 닫기', requires: 'project' }, { id: 's1', label: '', kind: 'separator' },
    { id: 'group.new', label: '새 Group', requires: 'project' }, { id: 'group.open', label: 'Group 열기…', requires: 'project' }, { id: 'group.recent', label: '최근 Group', kind: 'submenu', children: [], requires: 'project' }, { id: 'group.close', label: 'Group 닫기', requires: 'project' }, { id: 's2', label: '', kind: 'separator' },
    { id: 'flow.new', label: '새 플로우', requires: 'project' }, { id: 'flow.open', label: '플로우 열기…', requires: 'project' }, { id: 'flow.recent', label: '최근 플로우', kind: 'submenu', children: [], requires: 'project' }, { id: 'flow.close', label: '플로우 닫기', requires: 'flow' }, { id: 's3', label: '', kind: 'separator' }, { id: 'designer.close', label: 'Designer 닫기' },
  ] },
  { id: 'edit', label: '편집', items: [
    { id: 'undo', label: '실행 취소' }, { id: 'redo', label: '다시 실행' }, { id: 'e1', label: '', kind: 'separator' },
    { id: 'cut', label: '잘라내기', requires: 'selection' }, { id: 'copy', label: '복사', requires: 'selection' }, { id: 'paste', label: '붙여넣기', requires: 'flow' }, { id: 'delete', label: '삭제', requires: 'selection' }, { id: 'e2', label: '', kind: 'separator' },
    { id: 'arrange', label: '정렬 / 배치…', requires: 'flow' }, { id: 'autoArrange', label: '자동 배치', requires: 'flow' }, { id: 'e3', label: '', kind: 'separator' }, { id: 'selectAll', label: '전체 선택', requires: 'flow' },
  ] },
  { id: 'run', label: '실행', items: [
    { id: 'run.next', label: '다음 단계 실행', requires: 'flow' }, { id: 'run.all', label: '전체 실행', requires: 'flow' }, { id: 'r1', label: '', kind: 'separator' }, { id: 'run.stop', label: '실행 중지', requires: 'running' }, { id: 'run.restart', label: '재실행', requires: 'flow' }, { id: 'r2', label: '', kind: 'separator' }, { id: 'run.validate', label: '플로우 검증', requires: 'flow' },
  ] },
  { id: 'view', label: '보기', items: [
    { id: 'zoom.fit', label: '화면 맞춤' }, { id: 'zoom.out', label: '축소' }, { id: 'zoom.in', label: '확대' }, { id: 'zoom.100', label: '100%' }, { id: 'v1', label: '', kind: 'separator' },
    { id: 'density', label: '배치 밀도', kind: 'submenu', children: [{ id: 'density.veryWide', label: '매우 넓게' }, { id: 'density.wide', label: '넓게' }, { id: 'density.normal', label: '보통' }, { id: 'density.free', label: '자율형' }] },
  ] },
  { id: 'window', label: '창', items: [
    { id: 'window.left', label: '좌측 패널', kind: 'check' }, { id: 'window.right', label: '우측 도구 패널', kind: 'check' }, { id: 'window.inspector', label: 'Inspector', kind: 'check' }, { id: 'window.minimap', label: '미니맵', kind: 'check' }, { id: 'w1', label: '', kind: 'separator' },
    { id: 'window.project', label: '프로젝트 탐색기', kind: 'tab' }, { id: 'window.file', label: '파일 탐색기', kind: 'tab' }, { id: 'w2', label: '', kind: 'separator' },
    { id: 'window.palette', label: '팔레트', kind: 'tab' }, { id: 'window.conversation', label: '대화', kind: 'tab' }, { id: 'window.git', label: 'Git', kind: 'tab' },
  ] },
  { id: 'help', label: '도움말', items: [{ id: 'help.shortcuts', label: '단축키' }, { id: 'help.guide', label: '사용 가이드' }, { id: 'h1', label: '', kind: 'separator' }, { id: 'help.about', label: 'FlowGate 정보' }] },
]
const root = ref<HTMLElement | null>(null)
const openIndex = ref<number | null>(null)
const openSub = ref<string | null>(null)
function disabled(item: Item) {
  if (item.id === 'run.next' || item.id === 'run.all' || item.id === 'run.restart') return props.running || !props.flowAvailable
  if (item.requires === 'project') return !props.projectAvailable
  if (item.requires === 'flow') return !props.flowAvailable
  if (item.requires === 'selection') return !props.hasSelection
  if (item.requires === 'running') return !props.running
  return false
}
function checked(id: string) { return id === 'window.left' ? !props.leftCollapsed : id === 'window.right' ? !props.rightCollapsed : true }
function activeTab(id: string) { return id === `window.${props.leftMode}` || id === `window.${props.rightTool}` }
function toggle(index: number) { openIndex.value = openIndex.value === index ? null : index; openSub.value = null }
function onHover(index: number) { if (openIndex.value !== null) { openIndex.value = index; openSub.value = null } }
function select(id: string) { emit('command', id); openIndex.value = null; openSub.value = null }
function focusItems() { const menu = root.value?.querySelectorAll('.fg-menu-pop')[0]; (menu?.querySelector('button:not(:disabled)') as HTMLButtonElement | null)?.focus() }
function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') { openIndex.value = null; openSub.value = null; (root.value?.querySelectorAll('.fg-menu-trigger')[0] as HTMLButtonElement | null)?.focus(); return }
  if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
    event.preventDefault(); const step = event.key === 'ArrowRight' ? 1 : -1
    if (openSub.value && event.key === 'ArrowLeft') { openSub.value = null; return }
    openIndex.value = ((openIndex.value ?? 0) + step + groups.length) % groups.length
    openSub.value = null
    setTimeout(focusItems, 0)
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    if (openIndex.value === null) openIndex.value = 0
    setTimeout(() => {
      const buttons = [...(root.value?.querySelectorAll('.fg-menu-pop button:not(:disabled)') ?? [])] as HTMLButtonElement[]
      const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
      buttons[(current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus()
    }, 0)
  }
  if (event.key === 'Enter' && openIndex.value === null) { openIndex.value = 0; setTimeout(focusItems, 0) }
}
function onDocumentPointer(event: PointerEvent) { if (root.value && !root.value.contains(event.target as Node)) { openIndex.value = null; openSub.value = null } }
onMounted(() => document.addEventListener('pointerdown', onDocumentPointer))
onUnmounted(() => document.removeEventListener('pointerdown', onDocumentPointer))
</script>

<style scoped>
.fg-menubar{height:34px;display:flex;align-items:stretch;gap:2px;padding:0 13px;background:#0b1a2c;border-bottom:1px solid var(--fg2-line-strong);color:#d9e7f5;position:relative;z-index:50}.fg-menu-wrap{position:relative}.fg-menu-trigger{height:100%;border:0;background:transparent;color:inherit;padding:0 13px;cursor:pointer}.fg-menu-trigger:hover,.fg-menu-trigger[aria-expanded=true]{background:#173657}.fg-menu-pop,.fg-sub-pop{position:absolute;top:100%;left:0;min-width:210px;padding:5px;background:#0c1c2e;border:1px solid #31577f;border-radius:7px;box-shadow:0 12px 30px #0009;z-index:70}.fg-sub-wrap{position:relative}.fg-sub-pop{top:0;left:100%}.fg-menu-item{display:flex;width:100%;align-items:center;gap:5px;text-align:left;white-space:nowrap;border:0;background:transparent;color:#d9e7f5;padding:7px 10px;font-size:12px;cursor:pointer}.fg-menu-item span:last-child:not(.fg-mark){margin-left:auto}.fg-menu-item:hover:not(:disabled),.fg-menu-item:focus-visible{background:#21466c}.fg-menu-item:disabled{opacity:.42;cursor:default}.fg-mark{width:14px;display:inline-block;color:#56a9ff}.fg-menu-separator{border-top:1px solid #29415b;margin:5px 4px}.fg-empty{padding:7px 10px;color:#91a8bf;font-size:11px}
</style>
