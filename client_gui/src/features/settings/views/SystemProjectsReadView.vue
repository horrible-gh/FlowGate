<template>
  <SettingsShell scope="system">
    <template #nav>
      <SettingsSubnav :items="systemNav" active="projects" />
    </template>

    <section class="fgs-panel" data-panel="system-projects">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="folder" size="lg" /></span>
        <div class="fgs-ptitle">
          <h2>{{ t('gui.settings.system_nav.projects') }}</h2>
          <p>{{ t('gui.settings.page_sub.system_projects') }}</p>
        </div>
        <div class="fgs-push">
          <button type="button" class="fgs-btn sm" :disabled="loading" data-testid="projects-refresh" @click="load">
            <SettingsIcon name="reset" size="sm" />{{ t('gui.settings.projects.refresh') }}
          </button>
        </div>
      </div>

      <div class="fgs-panel-body">
        <LoadingState v-if="loading" :label="t('gui.common.loading')" data-testid="projects-loading" />
        <ErrorState v-else-if="error" :title="t(`gui.settings.projects.errors.${error.kind}`)" :message="error.message" data-testid="projects-error">
          <template #action>
            <Button variant="secondary" data-testid="projects-retry" @click="load">{{ t('gui.common.retry') }}</Button>
          </template>
        </ErrorState>
        <Alert v-else-if="lost" tone="warning" data-testid="projects-session-lost">{{ t('gui.settings.projects.session_lost') }}</Alert>
        <template v-else-if="projects">
          <EmptyState v-if="!projects.length" :title="t('gui.settings.projects.empty')" data-testid="projects-empty" />
          <template v-else>
            <div class="fgs-bar">
              <div class="fgs-chips" role="group" :aria-label="t('gui.settings.projects.fields.status')">
                <button
                  v-for="f in FILTERS"
                  :key="f"
                  type="button"
                  class="fgs-chip"
                  :class="{ on: filter === f }"
                  :aria-pressed="filter === f"
                  :data-filter="f"
                  @click="filter = f"
                >
                  {{ t(`gui.settings.projects.filters.${f}`) }} <span class="n">{{ counts[f] }}</span>
                </button>
              </div>
            </div>
            <div class="fgs-dtable" style="--cols: minmax(140px, 1fr) minmax(160px, 1.3fr) minmax(180px, 2fr) 90px minmax(140px, 1.3fr) 24px">
              <div class="fgs-dtr head">
                <span>{{ t('gui.settings.projects.fields.id') }}</span>
                <span>{{ t('gui.settings.projects.fields.name') }}</span>
                <span>{{ t('gui.settings.projects.fields.description') }}</span>
                <span>{{ t('gui.settings.projects.fields.status') }}</span>
                <span>{{ t('gui.settings.projects.fields.modules') }}</span>
                <span />
              </div>
              <RouterLink
                v-for="p in shown"
                :key="p.project_id"
                :to="generalPath(p)"
                class="fgs-dtr row"
                :class="{ dim: !p.is_active }"
                :data-project-row="p.project_id"
              >
                <span class="mono">{{ p.project_id }}</span>
                <b>{{ p.project_name }}</b>
                <span class="desc">{{ p.description || '—' }}</span>
                <span><span class="fgs-pill" :class="p.is_active ? 'ok' : 'off'">{{ t(p.is_active ? 'gui.settings.projects.active' : 'gui.settings.projects.inactive') }}</span></span>
                <span class="mods">{{ p.modules.length ? p.modules.map((m) => m.name).join(', ') : '—' }}</span>
                <span class="fgs-chev"><SettingsIcon name="chev" size="sm" /></span>
              </RouterLink>
              <div v-if="!shown.length" class="fgs-dtr row static" data-testid="projects-no-match">
                <span class="fgs-muted">{{ t('gui.settings.no_match') }}</span>
              </div>
            </div>
            <div class="fgs-tablefoot">{{ t('gui.settings.projects.readonly_hint') }}</div>
          </template>
        </template>
      </div>
    </section>
  </SettingsShell>
</template>

<script setup lang="ts">
// 시스템 › 전체 프로젝트 (flowgate.v02.0076 T#5, D §2.8), system administrators only: the same
// GET /projects?status=all as the 프로젝트 selection area (../components/useProjectList — the same
// loading / failure / empty handling), shown read only with active and inactive projects. The
// 활성 / 비활성 filter is the screen's own display rule. A row opens that project's 기본 정보
// (/settings/project/:projectId/general). Opening this screen reads no project's Root or Git, and
// there is no create, archive / restore or delete: no button and no request for them.
//
// A token gone while the table is shown drops the rows with a notice, and 다시 불러오기 then sends
// nothing (useLiveRequests).
//
// The server answers this GET to any signed-in person (get_current_user only, D §0.2); showing the
// screen to administrators only narrows the GUI and does not close that existing gap.
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Alert from '@/components/ui/Alert.vue'
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useProjectList } from '../components/useProjectList'
import { useSystemNav } from '../components/systemNav'
import type { ProjectRow } from '../types'

type Filter = 'all' | 'active' | 'inactive'
const FILTERS: readonly Filter[] = ['all', 'active', 'inactive']

const { t } = useI18n()
const systemNav = useSystemNav()
const { projects, loading, error, lost, load } = useProjectList()
const filter = ref<Filter>('all')

const counts = computed<Record<Filter, number>>(() => {
  const list = projects.value ?? []
  const active = list.filter((p) => p.is_active).length
  return { all: list.length, active, inactive: list.length - active }
})

const shown = computed(() =>
  (projects.value ?? []).filter((p) => filter.value === 'all' || p.is_active === (filter.value === 'active')))

const generalPath = (p: ProjectRow) => `/settings/project/${encodeURIComponent(p.project_id)}/general`

onMounted(() => {
  void load()
})
</script>

<style scoped>
.fgs-bar {
  margin-bottom: 12px;
}

.mono {
  font-family: var(--fg2-font-mono, ui-monospace, monospace);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.desc,
.mods {
  color: #9fb3c9;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

a.fgs-dtr {
  color: inherit;
  text-decoration: none;
}
</style>
