<template>
  <div class="text-viewer">
    <div v-if="loading" class="text-viewer__state">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="text-viewer__state text-viewer__state--error">
      <span>{{ t('main.error.file_load_failed') }}</span>
    </div>
    <div v-else class="text-viewer__code-wrap" :class="{ 'text-viewer__code-wrap--wrap': wrapLines }">
      <div v-for="(line, idx) in highlightedLines" :key="idx" class="text-viewer__line-row">
        <span class="text-viewer__line-num" aria-hidden="true">{{ idx + 1 }}</span>
        <code class="text-viewer__line-code" v-html="line || '&nbsp;'"></code>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '@shared/api'

import hljs from 'highlight.js/lib/core'
import 'highlight.js/styles/github.css'

import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import scss from 'highlight.js/lib/languages/scss'
import json from 'highlight.js/lib/languages/json'
import yaml from 'highlight.js/lib/languages/yaml'
import ini from 'highlight.js/lib/languages/ini'
import sql from 'highlight.js/lib/languages/sql'
import bash from 'highlight.js/lib/languages/bash'
import powershell from 'highlight.js/lib/languages/powershell'
import dos from 'highlight.js/lib/languages/dos'
import dockerfile from 'highlight.js/lib/languages/dockerfile'

hljs.registerLanguage('python', python)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('scss', scss)
hljs.registerLanguage('json', json)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('ini', ini)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('powershell', powershell)
hljs.registerLanguage('dos', dos)
hljs.registerLanguage('dockerfile', dockerfile)

const EXT_LANG: Record<string, string> = {
  '.py': 'python',
  '.gd': 'python',      // gdscript not supported → python fallback
  '.js': 'javascript',
  '.jsx': 'javascript',
  '.ts': 'typescript',
  '.tsx': 'typescript',
  '.vue': 'xml',        // vue not supported → xml fallback
  '.html': 'xml',
  '.xml': 'xml',
  '.xsd': 'xml',
  '.svg': 'xml',
  '.css': 'css',
  '.scss': 'scss',
  '.json': 'json',
  '.yml': 'yaml',
  '.yaml': 'yaml',
  '.toml': 'ini',
  '.ini': 'ini',
  '.env': 'ini',
  '.sql': 'sql',
  '.sh': 'bash',
  '.bash': 'bash',
  '.ps1': 'powershell',
  '.bat': 'dos',
  '.cmd': 'dos',
}

const props = defineProps<{
  path: string
  projectId: string | null | undefined
  wrapLines?: boolean
}>()

const { t } = useI18n()

const content = ref('')
const loading = ref(false)
const error = ref(false)

function getLanguage(filePath: string): string | null {
  const basename = (filePath.split('/').pop()?.split('\\').pop() ?? '').toLowerCase()
  if (basename === 'dockerfile') return 'dockerfile'
  if (basename === '.env.example') return 'ini'
  const dot = basename.lastIndexOf('.')
  if (dot === -1) return null
  return EXT_LANG[basename.slice(dot)] ?? null
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

const language = computed(() => getLanguage(props.path ?? ''))

const logicalLines = computed((): string[] => {
  if (!content.value) return []
  const lines = content.value.split('\n')
  return lines[lines.length - 1] === '' ? lines.slice(0, -1) : lines
})

function highlightLine(line: string): string {
  const lang = language.value
  if (lang && line) {
    try {
      return hljs.highlight(line, { language: lang }).value
    } catch {
      // fall through to escaped plain text
    }
  }
  return escapeHtml(line)
}

const highlightedLines = computed((): string[] => logicalLines.value.map(highlightLine))

async function loadContent() {
  if (!props.path || !props.projectId) return
  loading.value = true
  error.value = false
  try {
    const url = `/api/v1/projects/${encodeURIComponent(props.projectId)}/files/src-content?path=${encodeURIComponent(props.path)}`
    const res = await api.get<string>(url, { responseType: 'text' })
    content.value = res.data ?? ''
  } catch {
    error.value = true
    content.value = ''
  } finally {
    loading.value = false
  }
}

watch(() => [props.path, props.projectId], loadContent, { immediate: true })

defineExpose({ loadContent })
</script>

<style scoped>
.text-viewer {
  height: 100%;
  overflow: auto;
  padding: 18px;
  scrollbar-gutter: stable;
  scrollbar-color: #94a3b8 #e2e8f0;
}

.text-viewer::-webkit-scrollbar {
  width: 28px;
  height: 14px;
}

.text-viewer::-webkit-scrollbar-track {
  background: #e2e8f0;
}

.text-viewer::-webkit-scrollbar-thumb {
  background: #94a3b8;
  border: 3px solid #e2e8f0;
  border-radius: 10px;
}

.text-viewer::-webkit-scrollbar-thumb:hover {
  background: #64748b;
}

.text-viewer::-webkit-scrollbar-corner {
  background: #e2e8f0;
}

.text-viewer__state {
  padding: 32px;
  text-align: center;
  opacity: 0.6;
  font-size: .8rem;
}

.text-viewer__state--error {
  color: var(--danger, #dc2626);
}

.text-viewer__code-wrap {
  font-family: 'JetBrains Mono', monospace;
  font-size: .8rem;
  line-height: 1.7;
  display: inline-flex;
  flex-direction: column;
  min-width: max-content;
}

.text-viewer__code-wrap--wrap {
  display: flex;
  width: 100%;
  min-width: 0;
}

.text-viewer__line-row {
  display: flex;
  align-items: flex-start;
  min-height: 1.7em;
}

.text-viewer__line-num {
  text-align: right;
  padding-right: 14px;
  min-width: 2.8em;
  flex-shrink: 0;
  user-select: none;
  white-space: nowrap;
  color: var(--text-s, #999);
  opacity: 0.45;
  border-right: 1px solid var(--border, #e0e0e0);
  margin-right: 14px;
}

.text-viewer__line-code {
  flex: 1;
  font-family: 'JetBrains Mono', monospace;
  font-size: .8rem;
  line-height: 1.7;
  white-space: pre;
  word-break: normal;
  background: transparent;
  padding: 0;
}

.text-viewer__code-wrap--wrap .text-viewer__line-code {
  min-width: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
