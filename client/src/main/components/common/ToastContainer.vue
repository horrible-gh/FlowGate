<template>
  <teleport to="body">
    <div class="toast-container">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        class="toast"
        :class="`toast-${toast.type}`"
      >
        {{ toast.message }}
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { useToast } from './useToast'

const { toasts } = useToast()
</script>

<style scoped>
.toast-container {
  position: fixed;
  top: calc(var(--hdr-h) + var(--tab-h) + 16px);
  right: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 2000;
}

.toast {
  position: relative;
  overflow: hidden;
  padding: 10px 16px;
  border-radius: 8px;
  font-size: .8125rem;
  font-weight: 500;
  line-height: 1.45;
  box-shadow:
    0 14px 36px rgba(15, 23, 42, .18),
    0 4px 12px rgba(15, 23, 42, .12);
  animation: slideIn 0.2s ease;
  min-width: 200px;
  max-width: 320px;
}

.toast::before {
  position: absolute;
  content: '';
  inset: 0 auto 0 0;
  width: 4px;
}

.toast-success {
  background: linear-gradient(180deg, #f0fdf4 0%, #dcfce7 100%);
  color: #16a34a;
  border: 1px solid rgba(22, 163, 74, .25);
}

.toast-success::before {
  background: #16a34a;
}

.toast-info {
  background: linear-gradient(180deg, #f0f9ff 0%, #e0f2fe 100%);
  color: #0284c7;
  border: 1px solid rgba(2, 132, 199, .25);
}

.toast-info::before {
  background: #0284c7;
}

.toast-warning {
  background: linear-gradient(180deg, #fffbeb 0%, #fef3c7 100%);
  color: #d97706;
  border: 1px solid rgba(217, 119, 6, .25);
}

.toast-warning::before {
  background: #d97706;
}

.toast-danger,
.toast-error {
  background: linear-gradient(180deg, #fff1f2 0%, #fee2e2 100%);
  color: #dc2626;
  border: 1px solid rgba(220, 38, 38, .25);
}

.toast-danger::before,
.toast-error::before {
  background: #dc2626;
}

@keyframes slideIn { from { transform: translateY(-10px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
</style>
