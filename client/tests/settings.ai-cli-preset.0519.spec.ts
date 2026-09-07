// flowgate.default.0519 T0007: canonical CLI preset command builder UI.
//
// AiProviderListEditor.vue's dialog renders a small panel (model name + permission choice +
// "Apply") for any CLI kind the catalog publishes a preset for (catalog.cli_presets). Applying
// calls the parent-supplied buildPresetCommand({kind, model_name, skip_permissions}) and only
// writes its result into form.cli_command on success — opening the dialog, switching kind, or
// editing the model/permission fields must never touch the command by themselves (T0007 §2/§4).
// AiSettingsView.vue/AiProjectSettingsView.vue own which endpoint that function calls (system
// vs. this project's own permission + project_id, T0007 §3) — the editor never guesses a URL.
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

function presetPanel(wrapper) {
  return wrapper.find('.ai-preset-panel')
}

function commandInput(wrapper) {
  return wrapper.findAll('input.mono')[0]
}

function presetModelInput(wrapper) {
  return presetPanel(wrapper).find('input.mono')
}

function presetPermissionSelect(wrapper) {
  return presetPanel(wrapper).find('select')
}

function presetApplyButton(wrapper) {
  return presetPanel(wrapper).findAll('button')[0]
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

describe('canonical CLI preset builder panel: visibility', () => {
  it('shows the panel only for a CLI kind the catalog publishes a preset for', async () => {
    const wrapper = await openAddForm(mountEditor())
    expect(presetPanel(wrapper).exists()).toBe(true) // default kind is claude

    await kindSelect(wrapper).setValue('custom')
    expect(presetPanel(wrapper).exists()).toBe(false)

    await kindSelect(wrapper).setValue('copilot')
    expect(presetPanel(wrapper).exists()).toBe(true)
  })

  it('is hidden for an API provider, which spawns no command', async () => {
    const wrapper = await openAddForm(mountEditor())
    await execTypeSelect(wrapper).setValue('api')
    expect(presetPanel(wrapper).exists()).toBe(false)
  })

  it('does nothing when the parent has not wired a builder (component default)', async () => {
    const wrapper = mount(AiProviderListEditor, {
      props: { providers: [], defaultIndex: -1, catalog: CATALOG },
      global: { plugins: [i18n] },
    })
    await openAddForm(wrapper)
    expect(presetPanel(wrapper).exists()).toBe(true) // catalog alone decides visibility
    await presetApplyButton(wrapper).trigger('click')
    await flushPromises()
    expect(commandInput(wrapper).element.value).toBe('')
    expect(presetPanel(wrapper).find('[role="alert"]').exists()).toBe(false)
  })
})

describe('canonical CLI preset builder panel: suggestion and non-interference', () => {
  it('suggests the kind\'s default model and refreshes it on kind switch', async () => {
    const wrapper = await openAddForm(mountEditor())
    expect(presetModelInput(wrapper).element.value).toBe('claude-opus-4-8')

    await kindSelect(wrapper).setValue('codex')
    expect(presetModelInput(wrapper).element.value).toBe('gpt-5.6-sol')
  })

  it('never writes into cli_command merely by opening the dialog', async () => {
    const wrapper = await openEditForm(
      mountEditor([cliProvider({ cli_command: 'claude --model hand-typed -p -' })]),
    )
    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')
  })

  it('never writes into cli_command from editing the model name or permission choice', async () => {
    const wrapper = await openAddForm(mountEditor())
    await commandInput(wrapper).setValue('claude --model hand-typed -p -')

    await presetModelInput(wrapper).setValue('some-other-model')
    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')

    await presetPermissionSelect(wrapper).setValue(true)
    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')
  })

  it('never writes into cli_command from a kind switch alone', async () => {
    const wrapper = await openAddForm(mountEditor())
    await commandInput(wrapper).setValue('claude --model hand-typed -p -')
    await kindSelect(wrapper).setValue('codex')
    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')
  })
})

describe('canonical CLI preset builder panel: applying', () => {
  it('sends kind, model name and permission choice to the builder', async () => {
    const buildPresetCommand = vi.fn().mockResolvedValue({ cli_command: 'claude --model m -p -' })
    const wrapper = await openAddForm(mountEditor([], buildPresetCommand))
    await presetModelInput(wrapper).setValue('claude-sonnet-5')
    await presetPermissionSelect(wrapper).setValue(true)
    await presetApplyButton(wrapper).trigger('click')

    expect(buildPresetCommand).toHaveBeenCalledWith({
      kind: 'claude', model_name: 'claude-sonnet-5', skip_permissions: true,
    })
  })

  it('replaces the command only once Apply resolves, and resyncs the permission checkbox', async () => {
    const buildPresetCommand = vi.fn().mockResolvedValue({
      cli_command: 'claude --model claude-opus-4-8 --dangerously-skip-permissions -p -',
    })
    const wrapper = await openAddForm(mountEditor([], buildPresetCommand))
    await commandInput(wrapper).setValue('claude --model old -p -')

    await presetApplyButton(wrapper).trigger('click')
    await flushPromises()

    expect(commandInput(wrapper).element.value)
      .toBe('claude --model claude-opus-4-8 --dangerously-skip-permissions -p -')
    expect(wrapper.text()).toContain(i18n.global.t('settings.ai.skip_permissions_warn'))
  })

  it('keeps the existing command and reports the reason when Apply fails', async () => {
    const buildPresetCommand = vi.fn().mockRejectedValue({
      response: { data: { detail: { errors: [{ field: 'model_name', reason: 'invalid_model_name' }] } } },
    })
    const wrapper = await openAddForm(mountEditor([], buildPresetCommand))
    await commandInput(wrapper).setValue('claude --model old -p -')

    await presetApplyButton(wrapper).trigger('click')
    await flushPromises()

    expect(commandInput(wrapper).element.value).toBe('claude --model old -p -')
    expect(presetPanel(wrapper).get('[role="alert"]').text()).toContain('invalid_model_name')
  })

  it('falls back to a generic message when the failure carries no error detail', async () => {
    const buildPresetCommand = vi.fn().mockRejectedValue(new Error('network down'))
    const wrapper = await openAddForm(mountEditor([], buildPresetCommand))

    await presetApplyButton(wrapper).trigger('click')
    await flushPromises()

    expect(presetPanel(wrapper).get('[role="alert"]').text())
      .toBe(i18n.global.t('settings.ai.preset_error_generic'))
  })

  it('disables the panel and dedupes clicks while a request is in flight', async () => {
    let resolveFn
    const buildPresetCommand = vi.fn(() => new Promise((resolve) => { resolveFn = resolve }))
    const wrapper = await openAddForm(mountEditor([], buildPresetCommand))

    await presetApplyButton(wrapper).trigger('click')
    expect(presetModelInput(wrapper).attributes('disabled')).toBeDefined()
    expect(presetPermissionSelect(wrapper).attributes('disabled')).toBeDefined()
    expect(presetApplyButton(wrapper).attributes('disabled')).toBeDefined()

    await presetApplyButton(wrapper).trigger('click')
    expect(buildPresetCommand).toHaveBeenCalledTimes(1)

    resolveFn({ cli_command: 'claude --model claude-opus-4-8 -p -' })
    await flushPromises()
    expect(presetApplyButton(wrapper).attributes('disabled')).toBeUndefined()
  })
})

describe('canonical CLI preset builder panel: stale responses', () => {
  it('ignores a response that lands after the dialog has been closed', async () => {
    let resolveFn
    const buildPresetCommand = vi.fn(() => new Promise((resolve) => { resolveFn = resolve }))
    const wrapper = await openAddForm(mountEditor([], buildPresetCommand))
    await presetApplyButton(wrapper).trigger('click')

    await wrapper.get('.modal-close').trigger('click')
    expect(wrapper.find('.modal-bg').exists()).toBe(false)

    resolveFn({ cli_command: 'claude --model stale --dangerously-skip-permissions -p -' })
    await flushPromises()

    await openAddForm(wrapper)
    expect(commandInput(wrapper).element.value).toBe('')
    expect(presetApplyButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('ignores a response that lands after the kind has changed', async () => {
    let resolveFn
    const buildPresetCommand = vi.fn(() => new Promise((resolve) => { resolveFn = resolve }))
    const wrapper = await openAddForm(mountEditor([], buildPresetCommand))
    await presetApplyButton(wrapper).trigger('click') // in flight for kind=claude

    await kindSelect(wrapper).setValue('codex')
    resolveFn({ cli_command: 'claude --model claude-opus-4-8 -p -' })
    await flushPromises()

    expect(commandInput(wrapper).element.value).toBe('')
    expect(presetApplyButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  // `claude` is published as both a CLI kind and an API kind, so leaving CLI for API leaves
  // form.kind untouched and the kind watcher never fires. Without an exec_type invalidation the
  // late response still lands on form.cli_command and silently replaces the command the user
  // had before they walked away from the CLI form (T0007 §2/§4 command preservation).
  it('ignores a response that lands after exec_type left CLI with the kind unchanged', async () => {
    let resolveFn
    const buildPresetCommand = vi.fn(() => new Promise((resolve) => { resolveFn = resolve }))
    const wrapper = await openEditForm(mountEditor(
      [cliProvider({ cli_command: 'claude --model hand-typed -p -' })], buildPresetCommand,
    ))
    await presetApplyButton(wrapper).trigger('click') // in flight for exec_type=cli, kind=claude

    await execTypeSelect(wrapper).setValue('api')
    expect(kindSelect(wrapper).element.value).toBe('claude') // the same kind on both sides
    expect(presetPanel(wrapper).exists()).toBe(false)

    resolveFn({ cli_command: 'claude --model stale --dangerously-skip-permissions -p -' })
    await flushPromises()

    await execTypeSelect(wrapper).setValue('cli')
    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')
    expect(presetApplyButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  // The round trip back to CLI restores every captured input (exec_type, kind, model,
  // permission), so only the generation counter can still tell the request is stale.
  it('ignores a response that lands after a CLI -> API -> CLI round trip', async () => {
    let resolveFn
    const buildPresetCommand = vi.fn(() => new Promise((resolve) => { resolveFn = resolve }))
    const wrapper = await openEditForm(mountEditor(
      [cliProvider({ cli_command: 'claude --model hand-typed -p -' })], buildPresetCommand,
    ))
    await presetApplyButton(wrapper).trigger('click')

    await execTypeSelect(wrapper).setValue('api')
    await execTypeSelect(wrapper).setValue('cli')
    resolveFn({ cli_command: 'claude --model stale --dangerously-skip-permissions -p -' })
    await flushPromises()

    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')
    expect(presetApplyButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  // The same walk-away, but with the request rejecting: the error belongs to a panel the user
  // has left, so it must not be waiting for them when they come back either.
  it('drops a failure that lands after exec_type left CLI', async () => {
    let rejectFn
    const buildPresetCommand = vi.fn(() => new Promise((_, reject) => { rejectFn = reject }))
    const wrapper = await openEditForm(mountEditor(
      [cliProvider({ cli_command: 'claude --model hand-typed -p -' })], buildPresetCommand,
    ))
    await presetApplyButton(wrapper).trigger('click')

    await execTypeSelect(wrapper).setValue('api')
    rejectFn(new Error('boom'))
    await flushPromises()

    await execTypeSelect(wrapper).setValue('cli')
    expect(commandInput(wrapper).element.value).toBe('claude --model hand-typed -p -')
    expect(presetPanel(wrapper).find('.ai-preset-error').exists()).toBe(false)
    expect(presetApplyButton(wrapper).attributes('disabled')).toBeUndefined()
  })
})

describe('canonical CLI preset builder panel: state does not leak across dialog sessions', () => {
  it('does not carry a typed model name into the next dialog session', async () => {
    const wrapper = mountEditor([cliProvider({ kind: 'codex', cli_command: 'codex exec -' })])
    await openAddForm(wrapper)
    await presetModelInput(wrapper).setValue('leaked-model')
    await wrapper.get('.modal-close').trigger('click')

    await openEditForm(wrapper, 0)
    expect(presetModelInput(wrapper).element.value).toBe('gpt-5.6-sol')
  })

  it('loads a saved command as-is on edit, never auto-overwriting it', async () => {
    const wrapper = await openEditForm(
      mountEditor([cliProvider({ kind: 'codex', cli_command: 'codex exec --skip-git-repo-check -' })]),
    )
    expect(commandInput(wrapper).element.value).toBe('codex exec --skip-git-repo-check -')
    expect(presetModelInput(wrapper).element.value).toBe('gpt-5.6-sol') // suggestion, not the saved command
  })
})

describe('AiSettingsView wires the system preset-command endpoint', () => {
  async function mountView(providers = []) {
    getRequest.mockResolvedValue({
      data: { providers, default_provider_id: providers[0]?.id ?? null, catalog: CATALOG },
    })
    const AiSettingsView = (await import('@/settings/views/system/AiSettingsView.vue')).default
    const wrapper = mount(AiSettingsView, { global: { plugins: [i18n] } })
    await flushPromises()
    return wrapper
  }

  it('calls the system cli-preset-command endpoint, not the provider save PUT', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, cli_command: 'claude --model claude-opus-4-8 -p -' } })
    const wrapper = await mountView([])

    await openAddForm(wrapper)
    await presetApplyButton(wrapper).trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/system/ai-settings/cli-preset-command', {
      kind: 'claude', model_name: 'claude-opus-4-8', skip_permissions: false,
    })
    expect(putRequest).not.toHaveBeenCalled()
  })
})

describe('AiProjectSettingsView wires the project preset-command endpoint', () => {
  function mountedStore(projectId) {
    const pinia = createPinia()
    setActivePinia(pinia)
    useSettingsStore().currentProjectId = projectId
    return pinia
  }

  it('calls this project\'s own cli-preset-command endpoint with its project_id', async () => {
    const pinia = mountedStore('proj_001')
    getRequest.mockResolvedValue({
      data: {
        mode: 'custom', providers: [], default_provider_id: null,
        effective: { source: 'project', providers: [], default_provider_id: null },
        catalog: CATALOG,
      },
    })
    postRequest.mockResolvedValue({ data: { ok: true, cli_command: 'codex --ask-for-approval on-request exec -' } })
    const AiProjectSettingsView = (await import('@/settings/views/project/AiProjectSettingsView.vue')).default
    const wrapper = mount(AiProjectSettingsView, { global: { plugins: [pinia, i18n] } })
    await flushPromises()

    await openAddForm(wrapper)
    await kindSelect(wrapper).setValue('codex')
    await presetApplyButton(wrapper).trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/proj_001/ai-settings/cli-preset-command',
      { kind: 'codex', model_name: 'gpt-5.6-sol', skip_permissions: false },
    )
    expect(putRequest).not.toHaveBeenCalled()
  })
})
