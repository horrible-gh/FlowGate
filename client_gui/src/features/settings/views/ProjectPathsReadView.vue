<template>
  <section class="fgs-panel" data-panel="project-paths">
    <div class="fgs-phead">
      <span class="fgs-tico"><SettingsIcon name="storage" size="lg" /></span>
      <h2>{{ t('gui.settings.project_nav.paths') }}</h2>
      <div class="fgs-push">
        <button type="button" class="fgs-btn sm" :disabled="loading" data-testid="paths-refresh" @click="load">
          <SettingsIcon name="reset" size="sm" />{{ t('gui.settings.projects.refresh') }}
        </button>
      </div>
    </div>

    <div class="fgs-panel-body">
      <LoadingState v-if="loading" :label="t('gui.common.loading')" data-testid="paths-loading" />
      <ErrorState v-else-if="error" :title="t(`gui.settings.projects.errors.paths_${error.kind}`)" :message="error.message" data-testid="paths-error">
        <template #action>
          <Button variant="secondary" data-testid="paths-retry" @click="load">{{ t('gui.common.retry') }}</Button>
        </template>
      </ErrorState>
      <div v-else-if="root" class="fgs-card" data-card="project-root">
        <div class="fgs-line">
          <div class="k">{{ t('gui.settings.projects.root.configured') }}</div>
          <div class="v mono" data-testid="root-configured">
            <template v-if="root.configured_value !== null">{{ root.configured_value }}</template>
            <span v-else class="fgs-muted">{{ t('gui.settings.projects.unset') }}</span>
          </div>
        </div>
        <div class="fgs-line">
          <div class="k">{{ t('gui.settings.projects.root.override') }}</div>
          <div class="v mono" data-testid="root-override">
            <template v-if="root.project_override !== null">{{ root.project_override }}</template>
            <span v-else class="fgs-muted">{{ t('gui.settings.projects.unset') }}</span>
          </div>
        </div>
        <div class="fgs-line">
          <div class="k">{{ t('gui.settings.projects.root.effective') }}</div>
          <div class="v mono" data-testid="root-effective">{{ root.effective_value }}</div>
        </div>
        <div class="fgs-line">
          <div class="k">{{ t('gui.settings.projects.root.source') }}</div>
          <div class="v" data-testid="root-source">{{ t(`gui.settings.projects.root.sources.${root.effective_source}`) }}</div>
        </div>
        <div class="fgs-line">
          <div class="k">{{ t('gui.settings.projects.root.env_override') }}</div>
          <div class="v" data-testid="root-env">{{ t(root.env_override ? 'gui.settings.projects.root.env_yes' : 'gui.settings.projects.root.env_no') }}</div>
        </div>
        <div v-if="root.env_override && root.project_override !== null" class="fgs-line">
          <span class="fgs-hint" data-testid="root-override-shadowed"><SettingsIcon name="info" size="sm" />{{ t('gui.settings.projects.root.shadowed') }}</span>
        </div>
        <div class="fgs-line">
          <span class="fgs-muted">{{ t('gui.settings.projects.root.readonly_hint') }}</span>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
// 프로젝트 › 저장 경로 (flowgate.v02.0076 T#5, D §2.3): GET /projects/{project_id}/storage-root only.
// configured_value · project_override · effective_value · effective_source · env_override are shown
// as the answer has them; a null value is 미설정. No path is worked out on the client. Neither
// GET /system/settings/storage-root nor the project settings / paths GETs are called, not even as a
// fallback when this GET fails. Read only: no input, no save, no move (paths PATCH and storage
// migrate are never sent). The layout mounts this view for a listed project only and remounts it for
// another one; an answer that arrives after that (or after sign-out / a lost role) is dropped.
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import SettingsIcon from '../SettingsIcon.vue'
import { getProjectStorageRoot } from '../api'
import { readErrorOf, type ReadError } from '../components/useProjectList'
import { STALE, useLiveRequests } from '../components/useLiveRequests'
import type { ProjectRow, ProjectStorageRoot } from '../types'

const props = defineProps<{ project: ProjectRow }>()

const SOURCES: readonly string[] = ['env', 'project_override', 'system_setting', 'default']

const { t } = useI18n()
const root = ref<ProjectStorageRoot | null>(null)
const loading = ref(false)
const error = ref<ReadError | null>(null)

const requests = useLiveRequests(() => {
  root.value = null
  loading.value = false
  error.value = null
})

const nullableText = (value: unknown) => value === null || typeof value === 'string'

function isRoot(body: unknown, projectId: string): body is ProjectStorageRoot {
  const b = body as Partial<ProjectStorageRoot> | null
  return !!b && b.project_id === projectId && nullableText(b.configured_value) && nullableText(b.project_override)
    && typeof b.effective_value === 'string' && SOURCES.includes(String(b.effective_source))
    && typeof b.env_override === 'boolean'
}

/** The GET (first load, 다시 불러오기, 다시 시도). It never calls another path. */
async function load() {
  // No token or no administrator access any more: nothing is sent, and every project view is ended.
  const at = requests.begin()
  if (at === null) return
  const projectId = props.project.project_id
  loading.value = true
  error.value = null
  try {
    const body = await requests.live(at, getProjectStorageRoot(projectId))
    if (!isRoot(body, projectId)) throw new Error(t('gui.settings.projects.errors.bad_response'))
    root.value = body
  } catch (err) {
    if (err === STALE) return
    root.value = null
    error.value = readErrorOf(err)
  } finally {
    if (at === requests.current()) loading.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<style scoped>
.fgs-panel {
  margin-top: 14px;
}

.v.mono {
  font-family: var(--fg2-font-mono, ui-monospace, monospace);
  word-break: break-all;
}
</style>
