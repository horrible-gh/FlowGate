<template>
  <section class="fgs-panel" data-panel="project-general">
    <div class="fgs-phead">
      <span class="fgs-tico"><SettingsIcon name="folder" size="lg" /></span>
      <div class="fgs-ptitle">
        <h2>{{ t('gui.settings.project_nav.general') }}</h2>
        <p><b>{{ project.project_name }}</b> · {{ t('gui.settings.page_sub.project_general') }}</p>
      </div>
    </div>

    <div class="fgs-panel-body">
      <div class="fgs-sec">
        <div class="fgs-rows" data-card="project-general">
          <div class="fgs-row">
            <div class="fgs-row-label"><strong>{{ t('gui.settings.projects.fields.name') }}</strong></div>
            <div class="fgs-row-ctrl" data-testid="project-name">{{ project.project_name }}</div>
          </div>
          <div class="fgs-row">
            <div class="fgs-row-label"><strong>{{ t('gui.settings.projects.fields.description') }}</strong></div>
            <div class="fgs-row-ctrl" data-testid="project-description">
              <template v-if="project.description">{{ project.description }}</template>
              <span v-else class="fgs-muted">{{ t('gui.settings.projects.no_description') }}</span>
            </div>
          </div>
          <div class="fgs-row">
            <div class="fgs-row-label"><strong>{{ t('gui.settings.projects.fields.status') }}</strong></div>
            <div class="fgs-row-ctrl" data-testid="project-status">
              <span class="fgs-badge" :class="project.is_active ? 'teal' : 'dim'">{{ t(project.is_active ? 'gui.settings.projects.active' : 'gui.settings.projects.inactive') }}</span>
            </div>
          </div>
          <div class="fgs-row">
            <div class="fgs-row-label"><strong>{{ t('gui.settings.projects.fields.modules') }}</strong></div>
            <div class="fgs-row-ctrl" data-testid="project-modules">
              <template v-if="project.modules.length">
                <span v-for="m in project.modules" :key="m.name" class="fgs-badge" data-testid="project-module">{{ moduleLabel(m) }}</span>
              </template>
              <span v-else class="fgs-muted">{{ t('gui.settings.projects.no_modules') }}</span>
            </div>
          </div>
        </div>
        <p class="fgs-hintline readonly">{{ t('gui.settings.projects.general_readonly') }}</p>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
// 프로젝트 › 기본 정보 (flowgate.v02.0076 T#5, D §2.3): the selected row of the project list — name,
// description, active state and modules — read only. There is no separate detail API, no request
// of its own and no save of the name or description. The project ID is an internal key (group /
// document IDs, paths) and is not shown here (mockup ⑩, flowgate.v02.0077).
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
.readonly {
  margin: 10px 0 0;
}
</style>
