import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import GitStationWindow from '@/features/git/GitStationWindow.vue'
import { fetchStationBranches, createStationBranch, importStationBranch } from '@/features/git/api'
vi.mock('@/features/git/api', async original => ({ ...await original<typeof import('@/features/git/api')>(),
  fetchStationBranches: vi.fn(), createStationBranch: vi.fn(), importStationBranch: vi.fn() }))
registerAppMessages(i18n)
let w: VueWrapper | null = null
const rows = Array.from({ length: 100 }, (_, i) => ({ name: `branch-${i}`, kind: i ? 'local' : 'base', canBeSource:true, ahead:0, behind:0 }))
beforeEach(() => { vi.clearAllMocks(); i18n.global.locale.value = 'ko'; vi.mocked(fetchStationBranches).mockResolvedValue(rows) })
afterEach(() => { w?.unmount(); w=null; document.body.innerHTML='' })
async function open(props = {}) {
  w = mount(GitStationWindow, { props:{open:true,projectId:'p',readOnly:false,...props},global:{plugins:[i18n],stubs:{ModelessShell:{template:'<div><slot name="header"/><slot/></div>'}}} })
  await flushPromises()
}
describe('Git station', () => {
  it('loads only when opened, searches a hundred branches, and creates from the selected branch', async () => {
    await open({open:false})
    expect(fetchStationBranches).not.toHaveBeenCalled()
    await w!.setProps({open:true}); await flushPromises()
    expect(w!.findAll('[role=option]')).toHaveLength(100)
    await w!.find('input[type=search]').setValue('branch-99')
    expect(w!.findAll('[role=option]')).toHaveLength(1)
    await w!.find('[role=option]').trigger('click')
    await w!.find('input:not([type=search])').setValue('new-branch')
    await w!.find('input:not([type=search])').trigger('keydown.enter'); await flushPromises()
    expect(createStationBranch).toHaveBeenCalledWith('p','new-branch','branch-99')
  })
  it('drops a late catalog after switching projects', async () => {
    let finish!: (value: typeof rows) => void
    vi.mocked(fetchStationBranches).mockImplementationOnce(() => new Promise(resolve => {finish=resolve}))
    await open()
    await w!.setProps({projectId:'other'}); await flushPromises()
    finish([{...rows[0],name:'old-project'}]); await flushPromises()
    expect(w!.text()).not.toContain('old-project')
  })
  it('offers no create or import mutation in read-only mode', async () => {
    vi.mocked(fetchStationBranches).mockResolvedValue([{...rows[0],kind:'remote_only',canBeSource:false}])
    await open({readOnly:true})
    expect(w!.findAll('input')).toHaveLength(1)
    expect(w!.text()).not.toContain('로컬로 가져오기')
    expect(importStationBranch).not.toHaveBeenCalled()
  })
  it('does not leave a new project busy after an older create request', async () => {
    let finish!: () => void
    vi.mocked(createStationBranch).mockImplementationOnce(() => new Promise(resolve => {finish=resolve}))
    await open()
    await w!.find('input:not([type=search])').setValue('new-branch')
    await w!.find('input:not([type=search])').trigger('keydown.enter')
    await w!.setProps({projectId:'other'}); await flushPromises()
    expect(w!.find<HTMLInputElement>('input:not([type=search])').element.disabled).toBe(false)
    finish(); await flushPromises()
    expect(w!.text()).not.toContain('완료')
  })
})
