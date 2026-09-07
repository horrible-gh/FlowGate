// flowgate.default.0519 T0009: the CLI command magic tool.
//
// T0009 replaced the canonical-command panel with one small button at the right edge of the
// CLI command input. It fills the command for the kind on screen, always in unattended form
// (rej_01M1YTRN0SD379SG: "skip permission confirmation" is not a user-facing choice in
// FlowGate — every CLI run here is unattended, so the flag is only ever an internal request
// parameter, never a checkbox or an explanatory paragraph), leaving the literal `model_name`
// in it for the user to edit by hand — the dialog gains no model field, no permission picker
// and no apply row. The flag/template knowledge stays on the server, reached through the
// parent-supplied buildPresetCommand(); the editor never guesses a URL, and
// AiSettingsView/AiProjectSettingsView each wire their own endpoint.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiProviderListEditor from '@/settings/components/AiProviderListEditor.vue'
import { useSettingsStore } from '@/settings/stores/settings.js'

const { getRequest, putRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  putRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, putRequest, postRequest }))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn() }))

const CATALOG = {
  exec_types: ['cli', 'api'],
  kinds: { cli: ['claude', 'codex', 'copilot', 'custom'], api: ['claude'] },
  cli_presets: {
    claude: { default_model: 'claude-opus-4-8', supports_permission_skip: true },
    codex: { default_model: 'gpt-5.6-sol', supports_permission_skip: true },
    copilot: { default_model: 'claude-sonnet-5', supports_permission_skip: true },
  },
  cli_permission_skip: {
    default_enabled: false,
    rules: {
      claude: { skip: '--dangerously-skip-permissions', safe: '', markers: ['--dangerously-skip-permissions'] },
      codex: {
        skip: '--ask-for-approval never',
        safe: '--ask-for-approval on-request',
        markers: ['--ask-for-approval never'],
      },
    },
    examples: {},
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

function mountEditor(providers = [], buildPresetCommand = vi.fn()) {
  return mount(AiProviderListEditor, {
    props: {
      providers, defaultIndex: providers.length ? 0 : -1, catalog: CATALOG, buildPresetCommand,
    },
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

async function openEditForm(wrapper, row = 0) {
  const buttons = wrapper.findAll(`button[title="${i18n.global.t('common.edit')}"]`)
  await buttons[row].trigger('click')
  return wrapper
}

function commandInput(wrapper) {
  return wrapper.get('.cli-cmd-row').find('input.mono')
}

function magicButton(wrapper) {
  return wrapper.find('.cli-magic-btn')
}

// Scoped to the dialog: a view can carry its own selects outside it (e.g. the project
// screen's inherit/disabled/custom mode picker), which would otherwise shift plain
// wrapper.findAll('select') indices out from under this helper.
function execTypeSelect(wrapper) {
  return wrapper.get('.modal-bg').findAll('select')[0]
}

function kindSelect(wrapper) {
  return wrapper.get('.modal-bg').findAll('select')[1]
}

beforeEach(() => {
  getRequest.mockReset()
  putRequest.mockReset()
  postRequest.mockReset()
})

describe('CLI command magic tool: where it shows up', () => {
  it('sits inside the command row, as one button next to the command input', async () => {
    const wrapper = await openAddForm(mountEditor())
    const row = wrapper.get('.cli-cmd-row')

    expect(row.findAll('input').length).toBe(1)
    expect(row.findAll('button').length).toBe(1)
    expect(magicButton(wrapper).exists()).toBe(true)
    expect(magicButton(wrapper).attributes('title')).toBe(i18n.global.t('settings.ai.magic_tool'))
    expect(magicButton(wrapper).attributes('aria-label')).toBe(i18n.global.t('settings.ai.magic_tool'))
  })

  // T0009 rejected the panel that asked for a model name and a permission mode. The dialog
  // must carry the same fields it did before, plus this one button.
  it('adds no model field and no permission picker to the dialog', async () => {
    const wrapper = await openAddForm(mountEditor())
    const dialog = wrapper.get('.modal-bg')

    expect(dialog.findAll('input.mono').length).toBe(1) // the command input, nothing else
    expect(dialog.findAll('select').length).toBe(2) // exec_type + kind, as before
  })

  // rej_01M1YTRN0SD379SG: "skip permission confirmation" has no meaning as a user choice in
  // FlowGate (unattended runs have nobody to answer that prompt either way), so the checkbox
  // and the hint/warning text that used to hang under it are gone — the magic tool always
  // fills the unattended form internally, without asking or announcing it.
  it('shows no permission-skip checkbox or explanatory text', async () => {
    const wrapper = await openAddForm(mountEditor())
    const dialog = wrapper.get('.modal-bg')

    expect(dialog.findAll('input[type="checkbox"]').length).toBe(1) // just "enabled"
    expect(dialog.findAll('.form-hint').length).toBe(0)
  })

  it('is hidden for a kind the catalog publishes no preset for', async () => {
    const wrapper = await openAddForm(mountEditor())
    expect(magicButton(wrapper).exists()).toBe(true) // default kind is claude

    await kindSelect(wrapper).setValue('custom')
    expect(magicButton(wrapper).exists()).toBe(false)

    await kindSelect(wrapper).setValue('copilot')
    expect(magicButton(wrapper).exists()).toBe(true)
  })

  it('is hidden for an API provider, which spawns no command', async () => {
    const wrapper = await openAddForm(mountEditor())
    await execTypeSelect(wrapper).setValue('api')
    expect(wrapper.find('.cli-cmd-row').exists()).toBe(false)
  })

  it('is hidden when the screen wired no builder', async () => {
    const wrapper = mount(AiProviderListEditor, {
      props: { providers: [], defaultIndex: -1, catalog: CATALOG },
      global: { plugins: [i18n] },
    })
    await openAddForm(wrapper)
    expect(magicButton(wrapper).exists()).toBe(false)
  })
})

describe('CLI command magic tool: filling the command', () => {
  it('asks for the kind on screen with the literal model_name placeholder', async () => {
    const buildPresetCommand = vi.fn().mockResolvedValue({
      cli_command: 'codex --model model_name --ask-for-approval never --sandbox danger-full-access exec --skip-git-repo-check --json -',
    })
    const wrapper = await openAddForm(mountEditor([], buildPresetCommand))
    await kindSelect(wrapper).setValue('codex')

    await magicButton(wrapper).trigger('click')
    await flushPromises()

    expect(buildPresetCommand).toHaveBeenCalledWith({
      kind: 'codex', model_name: 'model_name', skip_permissions: true,
    })
    expect(commandInput(wrapper).element.value)
      .toBe('codex --model model_name --ask-for-approval never --sandbox danger-full-access exec --skip-git-repo-check --json -')
  })

  // rej_01M1YTRN0SD379SG: the button is for unmanned runs, so it always requests and applies
  // the skip-permissions form as an internal request parameter — there is no checkbox on
  // screen for it to tick, or for a prior state of to matter.
  it('always fills the skip-permissions form, with no checkbox to reflect it', async () => {
    const buildPresetCommand = vi.fn().mockResolvedValue({
      cli_command: 'claude --model model_name --dangerously-skip-permissions -p -',
    })
    const wrapper = await openAddForm(mountEditor([], buildPresetCommand))

    await magicButton(wrapper).trigger('click')
    await flushPromises()

    expect(buildPresetCommand).toHaveBeenCalledWith({
      kind: 'claude', model_name: 'model_name', skip_permissions: true,
    })
    expect(commandInput(wrapper).element.value)
      .toBe('claude --model model_name --dangerously-skip-permissions -p -')
    expect(wrapper.get('.modal-bg').findAll('input[type="checkbox"]').length).toBe(1) // just "enabled"
  })

  it('never fills the command by itself — only the click does', async () => {
    const buildPresetCommand = vi.fn().mockResolvedValue({ cli_command: 'generated' })
    const wrapper = await openEditForm(mountEditor(
      [cliProvider({ cli_command: 'claude --model hand-typed -p -' })], buildPresetCommand,
    ))
    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')

    await kindSelect(wrapper).setValue('copilot')
    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')
    expect(buildPresetCommand).not.toHaveBeenCalled()

    await openAddForm(wrapper)
    expect(commandInput(wrapper).element.value).toBe('')
    expect(buildPresetCommand).not.toHaveBeenCalled()
  })

  it('keeps the existing command and shows one error line when the fill fails', async () => {
    const buildPresetCommand = vi.fn().mockRejectedValue(new Error('boom'))
    const wrapper = await openEditForm(mountEditor(
      [cliProvider({ cli_command: 'claude --model hand-typed -p -' })], buildPresetCommand,
    ))

    await magicButton(wrapper).trigger('click')
    await flushPromises()

    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')
    expect(wrapper.find('.ai-magic-error').text())
      .toBe(i18n.global.t('settings.ai.magic_tool_error'))
    expect(magicButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('clears a previous error when the dialog is opened again', async () => {
    const buildPresetCommand = vi.fn().mockRejectedValue(new Error('boom'))
    const wrapper = await openEditForm(mountEditor([cliProvider()], buildPresetCommand))
    await magicButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.find('.ai-magic-error').exists()).toBe(true)

    await wrapper.get('.modal-close').trigger('click')
    await openEditForm(wrapper, 0)
    expect(wrapper.find('.ai-magic-error').exists()).toBe(false)
  })

  // rej_01M1YY7MQRQZAZG1: the magic tool leaves the literal model_name in the command for the
  // user to replace by hand — this hint is the only thing that tells them so.
  it('shows a hint to replace model_name after a fill, and clears it once replaced', async () => {
    const buildPresetCommand = vi.fn().mockResolvedValue({
      cli_command: 'claude --model model_name --dangerously-skip-permissions -p -',
    })
    const wrapper = await openAddForm(mountEditor([], buildPresetCommand))
    expect(wrapper.find('.form-hint').exists()).toBe(false) // empty command, nothing to replace yet

    await magicButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.find('.form-hint').text()).toBe(i18n.global.t('settings.ai.magic_model_hint'))

    await commandInput(wrapper).setValue('claude --model claude-opus-4-8 --dangerously-skip-permissions -p -')
    expect(wrapper.find('.form-hint').exists()).toBe(false)
  })

  // The reminder reflects the command text, not whether the button was ever clicked — typing
  // the placeholder by hand shows the same reminder.
  it('shows the same hint when the placeholder is typed by hand, without clicking the button', async () => {
    const wrapper = await openAddForm(mountEditor())
    await commandInput(wrapper).setValue('claude --model model_name -p -')
    expect(wrapper.find('.form-hint').text()).toBe(i18n.global.t('settings.ai.magic_model_hint'))
  })

  it('is disabled while a fill is in flight, so a second click cannot pile on', async () => {
    let resolveFn
    const buildPresetCommand = vi.fn(() => new Promise((resolve) => { resolveFn = resolve }))
    const wrapper = await openAddForm(mountEditor([], buildPresetCommand))

    await magicButton(wrapper).trigger('click')
    expect(magicButton(wrapper).attributes('disabled')).toBeDefined()
    await magicButton(wrapper).trigger('click')
    expect(buildPresetCommand).toHaveBeenCalledTimes(1)

    resolveFn({ cli_command: 'claude --model model_name -p -' })
    await flushPromises()
    expect(magicButton(wrapper).attributes('disabled')).toBeUndefined()
  })
})

describe('CLI command magic tool: late responses cannot overwrite the command', () => {
  it('ignores a response that lands after the kind changed', async () => {
    let resolveFn
    const buildPresetCommand = vi.fn(() => new Promise((resolve) => { resolveFn = resolve }))
    const wrapper = await openEditForm(mountEditor(
      [cliProvider({ cli_command: 'claude --model hand-typed -p -' })], buildPresetCommand,
    ))
    await magicButton(wrapper).trigger('click')

    await kindSelect(wrapper).setValue('codex')
    resolveFn({ cli_command: 'claude --model model_name -p -' })
    await flushPromises()

    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')
    expect(magicButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  // claude is published as a CLI kind and an API kind, so leaving CLI leaves form.kind alone
  // and the kind watcher never fires. Only the exec_type reset catches this walk-away.
  it('ignores a response that lands after exec_type left CLI with the kind unchanged', async () => {
    let resolveFn
    const buildPresetCommand = vi.fn(() => new Promise((resolve) => { resolveFn = resolve }))
    const wrapper = await openEditForm(mountEditor(
      [cliProvider({ cli_command: 'claude --model hand-typed -p -' })], buildPresetCommand,
    ))
    await magicButton(wrapper).trigger('click')

    await execTypeSelect(wrapper).setValue('api')
    expect(kindSelect(wrapper).element.value).toBe('claude')
    expect(wrapper.find('.cli-cmd-row').exists()).toBe(false)

    resolveFn({ cli_command: 'claude --model model_name -p -' })
    await flushPromises()

    await execTypeSelect(wrapper).setValue('cli')
    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')
    expect(magicButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  // Every captured input (exec_type, kind) is back to what it was when the request went out,
  // so only the generation counter can still tell this response is stale.
  it('ignores a response that lands after a CLI -> API -> CLI round trip', async () => {
    let resolveFn
    const buildPresetCommand = vi.fn(() => new Promise((resolve) => { resolveFn = resolve }))
    const wrapper = await openEditForm(mountEditor(
      [cliProvider({ cli_command: 'claude --model hand-typed -p -' })], buildPresetCommand,
    ))
    await magicButton(wrapper).trigger('click')

    await execTypeSelect(wrapper).setValue('api')
    await execTypeSelect(wrapper).setValue('cli')
    expect(kindSelect(wrapper).element.value).toBe('claude')

    resolveFn({ cli_command: 'claude --model model_name -p -' })
    await flushPromises()

    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')
    expect(magicButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('drops a failure that lands after exec_type left CLI', async () => {
    let rejectFn
    const buildPresetCommand = vi.fn(() => new Promise((_, reject) => { rejectFn = reject }))
    const wrapper = await openEditForm(mountEditor(
      [cliProvider({ cli_command: 'claude --model hand-typed -p -' })], buildPresetCommand,
    ))
    await magicButton(wrapper).trigger('click')

    await execTypeSelect(wrapper).setValue('api')
    rejectFn(new Error('boom'))
    await flushPromises()

    await execTypeSelect(wrapper).setValue('cli')
    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')
    expect(wrapper.find('.ai-magic-error').exists()).toBe(false)
  })

  it('ignores a response that lands after the dialog closed and another row opened', async () => {
    let resolveFn
    const buildPresetCommand = vi.fn(() => new Promise((resolve) => { resolveFn = resolve }))
    const rows = [
      cliProvider({ cli_command: 'claude --model first -p -' }),
      cliProvider({ id: 'aip_def456', name: 'second', cli_command: 'claude --model second -p -' }),
    ]
    const wrapper = await openEditForm(mountEditor(rows, buildPresetCommand), 0)
    await magicButton(wrapper).trigger('click')

    await wrapper.get('.modal-close').trigger('click')
    await openEditForm(wrapper, 1)
    resolveFn({ cli_command: 'claude --model model_name -p -' })
    await flushPromises()

    expect(commandInput(wrapper).element.value).toBe('claude --model second -p -')
  })
})

describe('each settings screen wires its own endpoint', () => {
  it('AiSettingsView calls the system endpoint, not the provider save PUT', async () => {
    getRequest.mockResolvedValue({
      data: { providers: [], default_provider_id: null, catalog: CATALOG },
    })
    postRequest.mockResolvedValue({
      data: { ok: true, cli_command: 'claude --model model_name --dangerously-skip-permissions -p -' },
    })
    const AiSettingsView = (await import('@/settings/views/system/AiSettingsView.vue')).default
    const wrapper = mount(AiSettingsView, { global: { plugins: [i18n] } })
    await flushPromises()

    await openAddForm(wrapper)
    await magicButton(wrapper).trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/system/ai-settings/cli-preset-command', {
      kind: 'claude', model_name: 'model_name', skip_permissions: true,
    })
    expect(putRequest).not.toHaveBeenCalled()
    expect(commandInput(wrapper).element.value).toBe('claude --model model_name --dangerously-skip-permissions -p -')
  })

  it('AiProjectSettingsView calls this project\'s own endpoint with its project_id', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useSettingsStore().currentProjectId = 'proj_001'
    getRequest.mockResolvedValue({
      data: {
        mode: 'custom', providers: [], default_provider_id: null,
        effective: { source: 'project', providers: [], default_provider_id: null },
        catalog: CATALOG,
      },
    })
    postRequest.mockResolvedValue({
      data: {
        ok: true,
        cli_command: 'codex --model model_name --ask-for-approval never --sandbox danger-full-access exec --skip-git-repo-check --json -',
      },
    })
    const AiProjectSettingsView = (await import('@/settings/views/project/AiProjectSettingsView.vue')).default
    const wrapper = mount(AiProjectSettingsView, { global: { plugins: [pinia, i18n] } })
    await flushPromises()

    await openAddForm(wrapper)
    await kindSelect(wrapper).setValue('codex')
    await magicButton(wrapper).trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/proj_001/ai-settings/cli-preset-command',
      { kind: 'codex', model_name: 'model_name', skip_permissions: true },
    )
    expect(putRequest).not.toHaveBeenCalled()
  })
})
