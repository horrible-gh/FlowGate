<template>
  <Teleport to="body">
    <div
      v-if="visible"
      ref="menuEl"
      class="ctx-menu"
      :style="{ left: adjX + 'px', top: adjY + 'px' }"
      role="menu"
    >
      <slot />
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, watch, onUnmounted, nextTick } from 'vue'

const props = defineProps<{
  visible: boolean
  x: number
  y: number
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
}>()

const menuEl = ref<HTMLElement | null>(null)
const adjX = ref(props.x)
const adjY = ref(props.y)

function close() {
  emit('update:visible', false)
}

function onDocClick(e: MouseEvent) {
  if (menuEl.value && !menuEl.value.contains(e.target as Node)) {
    close()
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') close()
}

function onDocContextMenu() {
  close()
}

function addListeners() {
  document.addEventListener('click', onDocClick, true)
  document.addEventListener('contextmenu', onDocContextMenu, true)
  document.addEventListener('keydown', onKeydown)
}

function removeListeners() {
  document.removeEventListener('click', onDocClick, true)
  document.removeEventListener('contextmenu', onDocContextMenu, true)
  document.removeEventListener('keydown', onKeydown)
}

watch(
  () => props.visible,
  async (val) => {
    if (val) {
      adjX.value = props.x
      adjY.value = props.y
      await nextTick()
      if (menuEl.value) {
        const rect = menuEl.value.getBoundingClientRect()
        if (rect.right > window.innerWidth) adjX.value = props.x - rect.width
        if (rect.bottom > window.innerHeight) adjY.value = props.y - rect.height
      }
      addListeners()
    } else {
      removeListeners()
    }
  },
)

onUnmounted(removeListeners)
</script>

<style scoped>
.ctx-menu {
  position: fixed;
  z-index: 1000;
  width: 200px;
  min-width: 200px;
  padding: 4px 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.18);
}
</style>
