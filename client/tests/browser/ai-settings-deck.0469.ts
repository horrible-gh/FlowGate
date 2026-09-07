/**
 * flowgate.default.0469 — v3 시안 덱(8bqoacqs) 6화면 대조용 렌더 하네스.
 *
 * 시안과 같은 데이터(프로바이더 4개 · 실행 정책 3회)를 넣고 실제 AiSettingsView 를
 * 그대로 띄운다. 시안 HTML 과 같은 viewport 에서 나란히 열어 요소 유무와 computed
 * style 수치를 대조하기 위한 화면이며, 제품 코드는 건드리지 않는다.
 * (work-plan-deck.0395.ts · ai-invoke-loop.0417.ts 와 같은 방식.)
 *
 *   /tests/browser/ai-settings-deck.0469.html?view=list    → ① 프로바이더 목록
 *   /tests/browser/ai-settings-deck.0469.html?view=edit    → ② 편집 다이얼로그
 *   /tests/browser/ai-settings-deck.0469.html?view=add     → ③ 추가 다이얼로그
 *   /tests/browser/ai-settings-deck.0469.html?view=command → ④ 커맨드 보기
 *   /tests/browser/ai-settings-deck.0469.html?view=delete  → ⑤ 삭제 확인
 *   /tests/browser/ai-settings-deck.0469.html?view=drag    → ⑥ 드래그 이동 중
 *   ...&rows=empty|long|narrow                             → ①의 빈 목록/긴 이름 변형
 */
import { createApp, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '../../shared/i18n'
import api from '../../shared/api'
import '../../shared/variables.css'
import '../../shared/app.css'
import AiSettingsView from '../../src/settings/views/system/AiSettingsView.vue'

/* 서버 get_catalog() (ai_settings_service.py §_PERMISSION_SKIP_RULES) 와 같은 모양 */
const CATALOG = {
  exec_types: ['cli', 'api'],
  kinds: { cli: ['claude', 'copilot', 'codex', 'custom'], api: ['claude', 'openai', 'custom'] },
  host_os: 'nt',
  host_shell: 'powershell',
  cli_permission_skip: {
    default_enabled: false,
    rules: {
      claude: {
        skip: '--dangerously-skip-permissions',
        safe: '',
        markers: ['--dangerously-skip-permissions'],
      },
      codex: {
        skip: '--ask-for-approval never',
        safe: '--ask-for-approval on-request',
        markers: [
          '--ask-for-approval never',
          '--ask-for-approval=never',
          '--dangerously-bypass-approvals-and-sandbox',
        ],
      },
    },
  },
}

/* 시안 ①의 네 행과 같은 이름·종류·상태 (기본=1행, 4행은 API·비활성) */
const DECK_ROWS = [
  {
    id: 'aip_claude', name: '클로드 CLI', kind: 'claude', exec_type: 'cli', enabled: true,
    cli_command: 'claude -p --dangerously-skip-permissions',
    api_base_url: null, api_model: null, api_key_set: false, api_key_hint: null,
  },
  {
    id: 'aip_codex', name: '코덱스 CLI', kind: 'codex', exec_type: 'cli', enabled: true,
    cli_command: 'codex --ask-for-approval never exec --json -',
    api_base_url: null, api_model: null, api_key_set: false, api_key_hint: null,
  },
  {
    id: 'aip_gemini', name: '제미나이 CLI', kind: 'custom', exec_type: 'cli', enabled: true,
    cli_command: 'gemini -p',
    api_base_url: null, api_model: null, api_key_set: false, api_key_hint: null,
  },
  {
    id: 'aip_openai', name: 'OpenAI API', kind: 'openai', exec_type: 'api', enabled: false,
    cli_command: null, api_base_url: 'https://api.openai.com/v1', api_model: 'gpt-5.6-sol',
    api_key_set: true, api_key_hint: 'a1b2',
  },
]

const params = new URLSearchParams(location.search)
const view = params.get('view') || 'list'
const rowsMode = params.get('rows') || 'deck'
/* 시안은 ko 화면이다. 헤드리스 크롬의 navigator.language 를 따라가면 다른 언어가
   렌더되므로 명시적으로 고정한다(?lang=en|ja 로 §3.12 다국어 확인). */
;(i18n.global.locale as unknown as { value: string }).value = params.get('lang') || 'ko'

const LONG = '아주아주 긴 프로바이더 이름 클로드 오퍼스 파이브 원 확장 실행 프로파일 20260906'
function rowsFor(mode: string) {
  if (mode === 'empty') return []
  if (mode === 'long') {
    return DECK_ROWS.map((row, i) => (i === 0 ? { ...row, name: LONG + LONG } : row))
  }
  return DECK_ROWS
}

const rows = rowsFor(rowsMode)

api.defaults.adapter = async (config: any) => {
  const url = config.url ?? ''
  let data: any = {}
  if (url.includes('/system/ai-settings')) {
    data = {
      providers: rows.map((r) => ({ ...r })),
      default_provider_id: rows.length ? rows[0].id : null,
      catalog: CATALOG,
    }
  } else if (url.includes('/system/settings')) {
    data = { settings: { ai_repeat_count_max: '3' } }
  }
  return { data, status: 200, statusText: 'OK', headers: {}, config }
}

setActivePinia(createPinia())

/* 시안 페이지와 같은 바깥 뼈대: settings-shell(210px + 20px + 1fr, max 1100px) 안의
   settings-content 에 화면을 얹는다. 폭이 시안과 같아야 행 높이·잘림을 비교할 수 있다. */
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

function fireDrag(el: Element, type: string) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  el.dispatchEvent(event)
}

async function openView() {
  await sleep(150)
  if (view === 'edit') {
    document.querySelectorAll<HTMLElement>('.ai-row-btns button')[3]?.click()
  } else if (view === 'add') {
    findByText('button', i18n.global.t('settings.ai.add_provider') as string)?.click()
  } else if (view === 'command') {
    document.querySelectorAll<HTMLElement>('.ai-row-btns button')[2]?.click()
  } else if (view === 'delete') {
    /* 시안 ⑤는 3행("제미나이 CLI") 삭제다 */
    const third = document.querySelectorAll('.ai-row')[2]
    third?.querySelectorAll<HTMLElement>('.ai-row-btns button')[4]?.click()
  } else if (view === 'drag') {
    /* 시안 ⑥은 3행이 is-dragging, 2행이 drag-over */
    const list = document.querySelectorAll('.ai-row')
    if (list[2] && list[1]) {
      fireDrag(list[2], 'dragstart')
      await sleep(20)
      fireDrag(list[1], 'dragover')
    }
  }
  await sleep(200)
  ;(window as any).__harnessReady = true
}

void openView()
