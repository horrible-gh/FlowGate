/**
 * flowgate.default.0519 T0009 — CLI 커맨드 입력창 옆 매직툴의 실제 렌더 하네스.
 *
 * 실제 AiSettingsView 를 실제 CSS 로 띄우고, 서버 카탈로그/생성 endpoint 만 흉내낸다
 * (문자열은 server ai_settings_service.build_preset_command(kind, 'model_name') 의 실제
 * 반환값 그대로다). 헤드리스 크롬에서 버튼의 크기·위치와 진짜 클릭 결과를 실측하기
 * 위한 화면이며 제품 코드는 건드리지 않는다. (ai-settings-deck.0469.ts 와 같은 방식.)
 *
 *   /tests/browser/ai-magic-tool.0519.html?view=edit    → 편집 다이얼로그(claude CLI 행)
 *   /tests/browser/ai-magic-tool.0519.html?view=add     → 추가 다이얼로그
 *   ...&lang=en|ja                                      → 다국어 확인
 */
import { createApp, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '../../shared/i18n'
import api from '../../shared/api'
import '../../shared/variables.css'
import '../../shared/app.css'
import AiSettingsView from '../../src/settings/views/system/AiSettingsView.vue'

/* 서버 get_catalog() 와 같은 모양 — cli_presets 는 실측한 실제 값이다. */
const CATALOG = {
  exec_types: ['cli', 'api'],
  kinds: { cli: ['claude', 'codex', 'copilot', 'custom'], api: ['claude', 'openai', 'custom'] },
  host_os: 'nt',
  host_shell: 'powershell',
  cli_presets: {
    claude: { default_model: 'claude-opus-4-8', supports_permission_skip: true },
    codex: { default_model: 'gpt-5.6-sol', supports_permission_skip: true },
    copilot: { default_model: 'claude-sonnet-5', supports_permission_skip: true },
  },
  cli_permission_skip: {
    default_enabled: false,
    rules: {
      claude: {
        skip: '--dangerously-skip-permissions', safe: '',
        markers: ['--dangerously-skip-permissions'],
      },
      codex: {
        skip: '--ask-for-approval never', safe: '--ask-for-approval on-request',
        markers: ['--ask-for-approval never', '--ask-for-approval=never'],
      },
      copilot: { skip: '--allow-all', safe: '', markers: ['--allow-all'] },
    },
    examples: {},
  },
}

/* build_preset_command(kind, 'model_name', skip_permissions=...) 의 실제 반환값 */
const PRESET_COMMANDS: Record<string, { safe: string; skip: string }> = {
  claude: {
    safe: 'claude --model model_name -p -',
    skip: 'claude --model model_name --dangerously-skip-permissions -p -',
  },
  codex: {
    safe: 'codex --ask-for-approval on-request --sandbox workspace-write exec --skip-git-repo-check -c sandbox_workspace_write.network_access=true --json --model model_name -',
    skip: 'codex --ask-for-approval never --sandbox danger-full-access exec --skip-git-repo-check --json --model model_name -',
  },
  copilot: {
    safe: 'copilot --no-ask-user --model model_name --output-format=json',
    skip: 'copilot --allow-all --no-ask-user --model model_name --output-format=json',
  },
}

const ROWS = [
  {
    id: 'aip_claude', name: '클로드 CLI', kind: 'claude', exec_type: 'cli', enabled: true,
    cli_command: 'claude --model claude-opus-4-8 -p -',
    api_base_url: null, api_model: null, api_key_set: false, api_key_hint: null,
  },
  {
    id: 'aip_codex', name: '코덱스 CLI', kind: 'codex', exec_type: 'cli', enabled: true,
    cli_command: 'codex --ask-for-approval never exec --json -',
    api_base_url: null, api_model: null, api_key_set: false, api_key_hint: null,
  },
]

const params = new URLSearchParams(location.search)
const view = params.get('view') || 'edit'
;(i18n.global.locale as unknown as { value: string }).value = params.get('lang') || 'ko'

api.defaults.adapter = async (config: any) => {
  const url = config.url ?? ''
  let data: any = {}
  if (url.includes('cli-preset-command')) {
    const body = typeof config.data === 'string' ? JSON.parse(config.data) : (config.data || {})
    const preset = PRESET_COMMANDS[body.kind]
    if (!preset) {
      const err: any = new Error('unsupported_kind')
      err.response = { status: 422, data: { detail: { errors: [{ field: 'kind', reason: 'unsupported_kind' }] } } }
      throw err
    }
    data = { ok: true, cli_command: body.skip_permissions ? preset.skip : preset.safe }
  } else if (url.includes('/system/ai-settings')) {
    data = {
      providers: ROWS.map((r) => ({ ...r })),
      default_provider_id: ROWS[0].id,
      catalog: CATALOG,
    }
  } else if (url.includes('/system/settings')) {
    data = { settings: { ai_repeat_count_max: '3' } }
  }
  return { data, status: 200, statusText: 'OK', headers: {}, config }
}

setActivePinia(createPinia())

const app = createApp({
  render: () => h('div', { style: 'padding:24px 28px;' }, [
    h('div', { class: 'settings-shell' }, [
      h('nav', { class: 'settings-nav', style: 'height:320px;' }),
      h('div', { class: 'settings-content' }, [h(AiSettingsView)]),
    ]),
  ]),
})
app.use(i18n)
app.mount('#app')

const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms))

function findByText(selector: string, text: string) {
  return Array.from(document.querySelectorAll<HTMLElement>(selector))
    .find((el) => (el.textContent || '').includes(text)) || null
}

async function openView() {
  await sleep(150)
  if (view === 'add') {
    findByText('button', i18n.global.t('settings.ai.add_provider') as string)?.click()
  } else {
    document.querySelectorAll<HTMLElement>('.ai-row-btns button')[3]?.click()
  }
  await sleep(200)
  ;(window as any).__harnessReady = true
}

void openView()
