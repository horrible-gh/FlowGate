<template>
  <div class="req-layout">
    <AppHeader />
    <main class="req-main">
      <div class="req-container">
        <h1 class="req-title">{{ t('main.requirement.create.title') }}</h1>

        <div v-if="flashMessage" :class="['req-flash', flashOk ? 'req-flash--ok' : 'req-flash--err']">
          {{ flashMessage }}
        </div>

        <form class="req-form" @submit.prevent="submit">
          <!-- Project -->
          <label class="req-label">
            {{ t('main.requirement.create.project') }} *
            <select v-model="form.project" class="req-input" required @change="onProjectChange">
              <option value="" disabled>{{ t('main.requirement.create.select_project') }}</option>
              <option v-for="p in projects" :key="p.project" :value="p.project">
                {{ p.project }}
              </option>
            </select>
            <span v-if="projectsLoaded && projects.length === 0" class="req-warn">
              {{ t('main.requirement.create.projects_unavailable') }}
            </span>
          </label>

          <!-- Module -->
          <label class="req-label">
            {{ t('main.requirement.create.module') }}
            <select v-model="form.module" class="req-input">
              <option value="">{{ t('main.requirement.create.no_module') }}</option>
              <option v-for="m in currentModules" :key="m.id" :value="m.id">{{ m.label }}</option>
            </select>
          </label>

          <!-- Title -->
          <label class="req-label">
            {{ t('main.requirement.create.title_label') }} *
            <input
              v-model="form.title"
              class="req-input"
              type="text"
              maxlength="100"
              required
            />
          </label>

          <!-- Slug -->
          <label class="req-label">
            {{ t('main.requirement.create.slug') }}
            <input v-model="form.slug" class="req-input" type="text" />
          </label>

          <!-- Priority -->
          <label class="req-label">
            {{ t('main.requirement.create.priority') }}
            <select v-model="form.priority" class="req-input">
              <option value="low">{{ t('main.requirement.create.priority_low') }}</option>
              <option value="medium">{{ t('main.requirement.create.priority_medium') }}</option>
              <option value="high">{{ t('main.requirement.create.priority_high') }}</option>
            </select>
          </label>

          <!-- Body -->
          <label class="req-label req-label--full">
            {{ t('main.requirement.create.body') }}
            <textarea v-model="form.body" class="req-input req-textarea" rows="12" />
          </label>

          <div class="req-actions">
            <button type="submit" class="req-btn req-btn--primary" :disabled="submitting">
              {{ t('main.requirement.create.submit') }}
            </button>
            <button type="button" class="req-btn" @click="goBack">
              {{ t('main.requirement.create.cancel') }}
            </button>
          </div>
        </form>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getRequest, postUrlEncoded } from '@shared/api'
import AppHeader from '../components/AppHeader.vue'

interface ModuleItem {
  id: string
  label: string
}

interface ProjectItem {
  project: string
  modules: ModuleItem[]
}

const { t } = useI18n()
const router = useRouter()

const projects = ref<ProjectItem[]>([])
const currentModules = ref<Array<{ id: string; label: string }>>([])
const submitting = ref(false)
const flashMessage = ref('')
const flashOk = ref(false)
const projectsLoaded = ref(false)

const form = ref({
  project: '',
  module: '',
  title: '',
  slug: '',
  priority: 'medium',
  body: '',
})

onMounted(async () => {
  try {
    const res = await getRequest<unknown>('/api/v1/projects')
    const raw = res.data
    const list: unknown[] =
      Array.isArray(raw) ? raw :
      Array.isArray((raw as { data?: unknown[] })?.data) ? (raw as { data: unknown[] }).data :
      Array.isArray((raw as { projects?: unknown[] })?.projects) ? (raw as { projects: unknown[] }).projects :
      []
    projects.value = list.map((item) => {
      const it = item as Record<string, unknown>
      return {
        project: ((it.project ?? it.project_id) ?? '') as string,
        modules: Array.isArray(it.modules)
          ? (it.modules as Array<string | { name?: string; title?: string }>).map((m) =>
              typeof m === 'string' ? { id: m, label: m } : { id: (m.name ?? '') as string, label: ((m.title || m.name) ?? '') as string }
            )
          : [],
      }
    }).filter((p) => p.project !== '__SYSTEM__')
  } catch {
    projects.value = []
  } finally {
    projectsLoaded.value = true
  }
})

function onProjectChange() {
  form.value.module = ''
  const found = projects.value.find((p) => p.project === form.value.project)
  currentModules.value = found ? found.modules : []
}

async function submit() {
  submitting.value = true
  flashMessage.value = ''
  try {
    await postUrlEncoded('/api/v1/outbox/create', {
      project: form.value.project,
      module: form.value.module,
      title: form.value.title,
      slug: form.value.slug,
      priority: form.value.priority,
      body: form.value.body,
    })
    flashOk.value = true
    flashMessage.value = t('main.requirement.create.success')
    form.value = { project: '', module: '', title: '', slug: '', priority: 'medium', body: '' }
    currentModules.value = []
  } catch {
    flashOk.value = false
    flashMessage.value = t('main.requirement.create.error')
  } finally {
    submitting.value = false
  }
}

function goBack() {
  router.push('/')
}
</script>

<style>
*,
*::before,
*::after {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--bg, #f0f4f8);
  color: var(--text, #0f172a);
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
}
</style>

<style scoped>
.req-layout {
  display: grid;
  grid-template-rows: 48px 1fr;
  height: 100vh;
  overflow: hidden;
}

.req-main {
  overflow-y: auto;
  padding: 32px 16px;
}

.req-container {
  max-width: 720px;
  margin: 0 auto;
}

.req-title {
  font-size: 1.25rem;
  font-weight: 700;
  margin-bottom: 24px;
  color: var(--text, #0f172a);
}

.req-flash {
  padding: 10px 14px;
  border-radius: 6px;
  margin-bottom: 20px;
  font-size: 0.9rem;
}

.req-flash--ok {
  background: var(--success-l, #dcfce7);
  border: 1px solid var(--success, #16a34a);
  color: var(--success, #16a34a);
}

.req-flash--err {
  background: var(--danger-l, #fee2e2);
  border: 1px solid var(--danger, #dc2626);
  color: var(--danger, #dc2626);
}

.req-form {
  display: grid;
  gap: 16px;
}

.req-label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.875rem;
  color: var(--text-m, #94a3b8);
}

.req-label--full {
  grid-column: 1 / -1;
}

.req-input {
  background: var(--surface, #fff);
  color: var(--text, #0f172a);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 4px;
  padding: 6px 10px;
  font-size: 0.9rem;
  width: 100%;
}

.req-input:focus {
  outline: none;
  border-color: var(--primary, #2563eb);
}

.req-textarea {
  resize: vertical;
  min-height: 200px;
  font-family: 'Consolas', 'Monaco', monospace;
  line-height: 1.5;
}

.req-actions {
  display: flex;
  gap: 12px;
  margin-top: 8px;
}

.req-btn {
  padding: 8px 20px;
  border-radius: 4px;
  border: 1px solid var(--border, #e2e8f0);
  background: var(--surface, #fff);
  color: var(--text, #0f172a);
  cursor: pointer;
  font-size: 0.9rem;
}

.req-btn:hover {
  background: var(--surface-h, #f8fafc);
}

.req-btn--primary {
  background: var(--primary, #2563eb);
  color: #fff;
  border-color: var(--primary, #2563eb);
  font-weight: 600;
}

.req-btn--primary:hover {
  opacity: 0.85;
}

.req-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.req-warn {
  color: var(--danger, #dc2626);
  font-size: 0.8rem;
  margin-top: 4px;
}
</style>
