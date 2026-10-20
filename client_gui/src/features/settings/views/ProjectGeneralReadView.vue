<template>
  <section class="fgs-panel" data-panel="project-general">
    <div class="fgs-phead">
      <span class="fgs-tico"><SettingsIcon name="info" size="lg" /></span>
      <h2>{{ t('gui.settings.project_nav.general') }}</h2>
    </div>

    <div class="fgs-panel-body">
      <div class="fgs-card" data-card="project-general">
        <div class="fgs-line">
          <div class="k">{{ t('gui.settings.projects.fields.id') }}</div>
          <div class="v mono" data-testid="project-id">{{ project.project_id }}</div>
        </div>
        <div class="fgs-line">
          <div class="k">{{ t('gui.settings.projects.fields.name') }}</div>
          <div class="v" data-testid="project-name">{{ project.project_name }}</div>
        </div>
        <div class="fgs-line">
          <div class="k">{{ t('gui.settings.projects.fields.description') }}</div>
          <div class="v" data-testid="project-description">
            <template v-if="project.description">{{ project.description }}</template>
            <span v-else class="fgs-muted">{{ t('gui.settings.projects.no_description') }}</span>
          </div>
        </div>
        <div class="fgs-line">
          <div class="k">{{ t('gui.settings.projects.fields.status') }}</div>
          <div class="v" data-testid="project-status">
            <span class="fgs-pill" :class="project.is_active ? 'ok' : 'off'">{{ t(project.is_active ? 'gui.settings.projects.active' : 'gui.settings.projects.inactive') }}</span>
          </div>
        </div>
        <div class="fgs-line">
          <div class="k">{{ t('gui.settings.projects.fields.modules') }}</div>
          <div class="v" data-testid="project-modules">
            <template v-if="project.modules.length">
              <span v-for="m in project.modules" :key="m.name" class="module" data-testid="project-module">{{ moduleLabel(m) }}</span>
            </template>
            <span v-else class="fgs-muted">{{ t('gui.settings.projects.no_modules') }}</span>
          </div>
        </div>
        <div class="fgs-line">
          <span class="fgs-muted">{{ t('gui.settings.projects.general_readonly') }}</span>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
// 프로젝트 › 기본 정보 (flowgate.v02.0076 T#5, D §2.3): the selected row of the project list — ID,
// name, description, active state and modules — read only. There is no separate detail API, no
// request of its own and no save of the name or description.
import { useI18n } from 'vue-i18n'
import SettingsIcon from '../SettingsIcon.vue'
import type { ProjectModuleRef, ProjectRow } from '../types'

defineProps<{ project: ProjectRow }>()

const { t } = useI18n()

function moduleLabel(m: ProjectModuleRef): string {
  return m.title && m.title !== m.name ? `${m.title} (${m.name})` : m.name
}
</script>

<style scoped>
.fgs-panel {
  margin-top: 14px;
}

.v.mono {
  font-family: var(--fg2-font-mono, ui-monospace, monospace);
  word-break: break-all;
}

.module {
  display: inline-block;
  margin: 0 6px 4px 0;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  color: #a9bbce;
}
</style>
