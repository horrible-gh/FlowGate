<template>
  <div class="fg-toolbar" role="toolbar" aria-label="Designer 도구" data-test="designer-toolbar">
    <div class="fg-tool-group"><button type="button" @click="emit('command', 'undo')">↶ 실행 취소</button><button type="button" @click="emit('command', 'redo')">↷ 다시 실행</button></div>
    <div class="fg-tool-group"><button type="button" :disabled="!flowAvailable" @click="emit('command', 'arrange')">☷ 정렬 / 배치 ▾</button></div>
    <div class="fg-tool-group">
      <button type="button" data-test="designer-run-next" :disabled="running !== 'next' && (!flowAvailable || running !== null)" @click="emit('command', running === 'next' ? 'run.stop' : 'run.next')">{{ running === 'next' ? '■ 중지' : '▶ 다음 단계' }}</button>
      <button type="button" data-test="designer-run-all" :disabled="running !== 'all' && (!flowAvailable || running !== null)" @click="emit('command', running === 'all' ? 'run.stop' : 'run.all')">{{ running === 'all' ? '■ 중지' : '⏩ 전체 실행' }}</button>
    </div>
    <div class="fg-tool-group fg-view-tools">
      <button type="button" @click="emit('command', 'zoom.fit')">⛶ 화면 맞춤</button>
      <button type="button" @click="emit('command', 'zoom.out')">⊖ 축소</button>
      <button type="button" @click="emit('command', 'zoom.in')">⊕ 확대</button>
      <button type="button" data-test="designer-zoom" @click="emit('command', 'zoom.100')">{{ zoom }}% ▾</button>
      <div class="fg-density" aria-label="배치 밀도">
        <button v-for="item in densities" :key="item.id" type="button" :title="item.label" :aria-label="item.label" :aria-pressed="density === item.id" @click="emit('command', `density.${item.id}`)">{{ item.glyph }}</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ flowAvailable: boolean; running: 'next' | 'all' | null; zoom: number; density: string }>()
const emit = defineEmits<{ command: [id: string] }>()
const densities = [
  { id: 'veryWide', label: '매우 넓게', glyph: '▦' }, { id: 'wide', label: '넓게', glyph: '▦' },
  { id: 'normal', label: '보통', glyph: '▦' }, { id: 'free', label: '자율형', glyph: '◇' },
]
</script>

<style scoped>
.fg-toolbar{height:66px;display:flex;align-items:center;gap:0;overflow-x:auto;background:#0a1729;padding:0 12px;color:#d9e7f5;border-bottom:1px solid var(--fg2-line-strong)}.fg-tool-group{display:flex;align-items:center;gap:3px;padding:0 10px;border-right:1px solid #29415b;height:34px;white-space:nowrap}.fg-tool-group:last-child{border-right:0}.fg-toolbar button{border:1px solid transparent;border-radius:6px;background:transparent;color:inherit;padding:7px 9px;cursor:pointer;white-space:nowrap;font-size:12px}.fg-toolbar button:hover:not(:disabled),.fg-toolbar button:focus-visible{background:#193b60;border-color:#31577f}.fg-toolbar button:disabled{opacity:.42;cursor:default}.fg-density{display:flex;gap:2px;margin-left:8px;background:#10243a;border-radius:6px}.fg-density button{padding:6px;font-size:15px}.fg-density button[aria-pressed=true]{background:#245395;color:white}
</style>
