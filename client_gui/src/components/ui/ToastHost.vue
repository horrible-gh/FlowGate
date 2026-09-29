<template>
  <Teleport to="body">
    <div v-if="toasts.length" class="ui-toast-host" :style="{ zIndex: hostZIndex }">
      <Toast
        v-for="item in toasts"
        :key="item.id"
        :tone="item.tone"
        :duration="item.duration"
        :dismiss-label="dismissLabel"
        @dismiss="emit('dismiss', item.id)"
      >
        {{ item.message }}
      </Toast>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
// Domain-free stacking region for Toast (T0008 §12 option B). Owns layout/position/
// stacking only. The caller owns the `toasts` array -- its own message queue, its own
// decision about when an item appears or disappears -- and this component never invents
// one of its own: no global event bus, no store, no server-driven notifications, no
// NotificationCenter integration (T0008 §12 "확장하지 않는다"). A `<div v-for>` in normal
// flex flow is the entire "stack without overlap" mechanism (T0008 §5.3) -- no absolute
// positioning per item, so two or a dozen toasts simply lay out one after another.
import { ref, watch } from 'vue'
import Toast, { type ToastTone } from './Toast.vue'
import { allocateOverlayZIndex } from '../../core/overlay/zIndex'

export interface ToastHostItem {
  id: string | number
  message: string
  tone?: ToastTone
  duration?: number | null
}

const props = withDefaults(defineProps<{ toasts: ToastHostItem[]; dismissLabel?: string }>(), {
  dismissLabel: 'Dismiss',
})
const emit = defineEmits<{ dismiss: [id: string | number] }>()

// Allocated from the same shared counter ModalShell/ModelessShell use (core/overlay/
// zIndex.ts), so a toast host still renders above whatever Modal/Modeless opened before
// it -- consistent with "나중에 뜬 것이 위" everywhere else in this overlay layer.
//
// A one-shot `computed` here would only ever allocate once: if a Modal opens *after*
// the host first appears, the Modal's z-index becomes higher and the host is stuck
// below it forever, since a dependency-free computed never re-runs. A length-growth
// check isn't enough either: the caller owns the queue (T0008 §12), so a new toast can
// land in a same-length update -- an atomic replace (`[{id:1}]` -> `[{id:2}]`) or a
// same-tick push+shift on a max-size queue never changes `toasts.length`. So this
// tracks item identity instead: whenever the current id set contains an id that wasn't
// present before, a toast just became newly relevant and the host re-allocates -- the
// same "re-allocate on the event that makes this overlay relevant again" pattern
// ModalShell uses for `open` going false -> true.
const hostZIndex = ref(0)
let previousIds = new Set<string | number>()
watch(
  () => props.toasts.map((item) => item.id),
  (ids) => {
    if (ids.some((id) => !previousIds.has(id))) hostZIndex.value = allocateOverlayZIndex()
    previousIds = new Set(ids)
  },
  { immediate: true },
)
</script>

<style scoped>
.ui-toast-host {
  position: fixed;
  top: 20px;
  right: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none;
}

.ui-toast-host :deep(.ui-toast) {
  pointer-events: auto;
}
</style>
