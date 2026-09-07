// flowgate.default.0371 T0014 (NR0007 §5): "skip the permission confirmation" used to be a
// word inside the suggested CLI command, so every provider registered the easy way ran
// without permission checks and nobody had chosen that. The screen briefly rendered it as
// its own control (unticked for a new provider), and said so on a row that does skip.
//
// flowgate.default.0519 TR0010 rev2 (rej_01M1YTRN0SD379SG): that control is gone again. The
// human rejection said "skip permission confirmation" has no meaning as a user choice in
// FlowGate — every CLI run here is unattended, so nobody is ever there to answer that
// confirmation prompt either way — and asked for it to become purely an internal detail (the
// magic tool always requests the unattended form) with no checkbox and no hint/warning text
// under it. The `describe('permission-skip control in the provider editor', ...)` block that
// used to live in this file tested that now-removed checkbox; it has been replaced below with
// a regression test that the control stays gone. The pure catalog-driven functions and the
// list row's read-only status badge are unaffected and still tested as before.
//
// The flags are not hard-coded on this side: they come down with the settings catalog, so
// these tests feed the same shape the server publishes.
//
// 0469 T4: the list row no longer renders a text badge for "skip" — it renders a
// `.ai-badge-skip` icon that carries the same wording as a hover tooltip (title/data-tip) and
// an always-on `aria-label` (§5 accessibility requirement), so the visibility tests below
// check the icon and its aria-label instead of `wrapper.text()`.
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiProviderListEditor from '@/settings/components/AiProviderListEditor.vue'
import {
  hasPermissionSkip,
  permissionSkipRule,
  setPermissionSkip,
} from '@/settings/components/aiPermissionSkip'

vi.mock('@shared/api', () => ({ getRequest: vi.fn(), putRequest: vi.fn() }))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn() }))

const CLAUDE_SKIP = '--dangerously-skip-permissions'

const CATALOG = {
  exec_types: ['cli', 'api'],
  kinds: { cli: ['claude', 'codex', 'copilot', 'custom'], api: ['claude'] },
  cli_permission_skip: {
    default_enabled: false,
    rules: {
      claude: { skip: CLAUDE_SKIP, safe: '', markers: [CLAUDE_SKIP] },
      codex: {
        skip: '--ask-for-approval never',
        safe: '--ask-for-approval on-request',
        markers: [
          '--ask-for-approval never',
          '--ask-for-approval=never',
          '--dangerously-bypass-approvals-and-sandbox',
          '--yolo',
        ],
      },
    },
    examples: {
      claude: { nt: `claude ${CLAUDE_SKIP} -p -`, posix: `claude ${CLAUDE_SKIP} -p -` },
      codex: { nt: 'codex --ask-for-approval never exec -', posix: 'codex --ask-for-approval never exec -' },
    },
  },
}

function cliProvider(extra = {}) {
  return {
    id: 'aip_abc123',
    name: 'claude cli',
    exec_type: 'cli',
    kind: 'claude',
    enabled: true,
    cli_command: 'claude --model claude-opus-4-8 -p -',
    api_base_url: null,
    api_model: null,
    api_key_set: false,
    api_key_hint: null,
    ...extra,
  }
}

function mountEditor(providers = []) {
  return mount(AiProviderListEditor, {
    props: { providers, defaultIndex: 0, catalog: CATALOG },
    global: { plugins: [i18n] },
  })
}

async function openAddForm(wrapper) {
  const add = wrapper.findAll('button').find(
    (b) => b.text().includes(i18n.global.t('settings.ai.add_provider')),
  )
  await add.trigger('click')
  return wrapper
}

async function openEditForm(wrapper) {
  await wrapper.find('button[title="' + i18n.global.t('common.edit') + '"]').trigger('click')
  return wrapper
}

describe('permission-skip rules (catalog driven)', () => {
  it('reads the rule off the catalog, and has none for a CLI without a known flag', () => {
    expect(permissionSkipRule(CATALOG, 'claude').skip).toBe(CLAUDE_SKIP)
    expect(permissionSkipRule(CATALOG, 'copilot')).toBeNull()
    expect(permissionSkipRule({}, 'claude')).toBeNull()
  })

  it('detects every spelling that means "do not ask"', () => {
    expect(hasPermissionSkip(CATALOG, 'claude', `claude ${CLAUDE_SKIP} -p -`)).toBe(true)
    expect(hasPermissionSkip(CATALOG, 'codex', 'codex --ask-for-approval=never exec -')).toBe(true)
    expect(hasPermissionSkip(CATALOG, 'codex', 'codex --yolo exec -')).toBe(true)
  })

  it('does not mistake a longer word for the flag', () => {
    expect(hasPermissionSkip(CATALOG, 'codex', 'codex --yolo-mode exec -')).toBe(false)
    expect(hasPermissionSkip(CATALOG, 'codex', 'codex --ask-for-approval never-mind exec -')).toBe(false)
    expect(hasPermissionSkip(CATALOG, 'claude', `claude ${CLAUDE_SKIP}-not -p -`)).toBe(false)
  })

  it('puts the flag before the trailing stdin dash, and takes it back out again', () => {
    const base = 'claude --model m -p -'
    const on = setPermissionSkip(CATALOG, 'claude', base, true)
    expect(on).toBe(`claude ${CLAUDE_SKIP} --model m -p -`)
    expect(setPermissionSkip(CATALOG, 'claude', on, false)).toBe(base)
  })

  it('rewrites the codex policy where it stands instead of appending a second one', () => {
    const base = 'codex --ask-for-approval on-request --sandbox workspace-write exec -'
    const on = setPermissionSkip(CATALOG, 'codex', base, true)
    expect(on).toBe('codex --ask-for-approval never --sandbox workspace-write exec -')
    expect(setPermissionSkip(CATALOG, 'codex', on, false)).toBe(base)
  })

  it('leaves a kind with no known flag, and an empty command, alone', () => {
    const copilot = 'copilot --output-format=json'
    expect(setPermissionSkip(CATALOG, 'copilot', copilot, true)).toBe(copilot)
    expect(setPermissionSkip(CATALOG, 'claude', '', true)).toBe('')
  })
})

// flowgate.default.0519 TR0010 rev2 (rej_01M1YTRN0SD379SG): the checkbox this describe block
// used to exercise is gone — "skip permission confirmation" is no longer offered as a user
// choice anywhere in the add/edit dialog, for any kind, in either direction (typing the flag
// by hand no longer ticks anything either, because there is nothing left to tick). The magic
// tool still always requests the unattended form (see settings.ai-magic-tool.0519.spec.ts);
// that is an internal parameter of the request now, not a control on screen.
describe('permission-skip control is not offered in the provider editor', () => {
  it('a new provider has no permission-skip checkbox, hint or warning text', async () => {
    const wrapper = await openAddForm(mountEditor())
    expect(wrapper.get('.modal-bg').findAll('input[type="checkbox"]').length).toBe(1) // just "enabled"
    expect(wrapper.get('.modal-bg').findAll('.form-hint').length).toBe(0)
  })

  it('an existing row that already skips still shows no checkbox when edited', async () => {
    const wrapper = await openEditForm(
      mountEditor([cliProvider({ cli_command: `claude ${CLAUDE_SKIP} --model m -p -` })]),
    )
    expect(wrapper.get('.modal-bg').findAll('input[type="checkbox"]').length).toBe(1) // just "enabled"
  })

  it('typing the flag into the command by hand does not conjure a checkbox', async () => {
    const wrapper = await openAddForm(mountEditor())
    await wrapper.find('input.mono').setValue(`claude ${CLAUDE_SKIP} -p -`)
    expect(wrapper.get('.modal-bg').findAll('input[type="checkbox"]').length).toBe(1) // just "enabled"
  })

  it('is absent for every kind, including one with no known flag', async () => {
    const wrapper = await openAddForm(mountEditor())
    for (const kind of ['claude', 'copilot', 'custom']) {
      await wrapper.findAll('select')[1].setValue(kind)
      expect(wrapper.get('.modal-bg').findAll('input[type="checkbox"]').length).toBe(1) // just "enabled"
    }
  })

  it('an API provider, which spawns no command, is unaffected either way', async () => {
    const wrapper = await openAddForm(mountEditor())
    await wrapper.findAll('select')[0].setValue('api')
    expect(wrapper.text()).toContain(i18n.global.t('settings.ai.label_api_model'))
    expect(wrapper.get('.modal-bg').findAll('input[type="checkbox"]').length).toBe(1) // just "enabled"
  })
})

describe('permission-skip visibility in the provider list', () => {
  it('marks a row that runs without permission checks', () => {
    const wrapper = mountEditor([
      cliProvider({ cli_command: `claude ${CLAUDE_SKIP} -p -` }),
    ])
    const badge = wrapper.find('.ai-badge-skip')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('aria-label')).toBe(i18n.global.t('settings.ai.skip_permissions_badge'))
  })

  it('says nothing on a row that still asks', () => {
    const wrapper = mountEditor([cliProvider()])
    expect(wrapper.find('.ai-badge-skip').exists()).toBe(false)
  })

  it('says nothing on an API provider, which spawns nothing', () => {
    const wrapper = mountEditor([
      cliProvider({ exec_type: 'api', kind: 'claude', cli_command: null, api_model: 'gpt-5.6-sol' }),
    ])
    expect(wrapper.find('.ai-badge-skip').exists()).toBe(false)
  })
})
