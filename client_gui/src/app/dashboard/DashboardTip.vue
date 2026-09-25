<template>
  <div v-if="visible" class="dash-tip" role="note">
    <span class="dash-tip-mark" aria-hidden="true">💡</span>
    <strong>{{ t('gui.dashboard.tip_label') }}</strong>
    <span class="dash-tip-text">{{ t('gui.dashboard.tip_text') }}</span>
    <IconButton class="dash-tip-close" size="sm" variant="ghost" :label="t('gui.dashboard.tip_close')" @click="visible = false">×</IconButton>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import IconButton from '@/components/ui/IconButton.vue'

const { t } = useI18n()
// Dismissal is per page view; nothing is persisted.
const visible = ref(true)
</script>

<style scoped>
.dash-tip {
  min-height: 54px;
  margin-top: 28px;
  padding: 8px 20px;
  display: flex;
  align-items: center;
  gap: 14px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 9px;
  background: rgba(12, 24, 38, 0.78);
  font-size: 12px;
  color: #8fa5bd;
}

.dash-tip-mark {
  font-size: 19px;
  color: #ffd659;
}

.dash-tip strong {
  flex: 0 0 auto;
  font-size: 12px;
  color: #ffd75f;
}

.dash-tip-text {
  min-width: 0;
}

.dash-tip-close {
  margin-left: auto;
  padding: 0 4px;
  border: 0;
  background: none;
  color: #8da0b7;
  font-size: 22px;
  line-height: 1;
  cursor: pointer;
}

/* IconButton's own `.ui-icon-btn--sm` rule fixes a 28x28 box; HEAD's plain button had no
   forced size and sized itself from the glyph + padding above. Combined selector guarantees
   the override wins regardless of build order. */
.dash-tip-close.ui-icon-btn {
  width: auto;
  height: auto;
}
</style>
