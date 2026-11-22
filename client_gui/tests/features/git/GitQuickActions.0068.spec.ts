import { afterEach, describe, it, expect } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import { createFakeGitSource, G, P } from './fakeGitSource'
import { createFakeWorktreeSource, worktreeFile, worktreeStatus } from './fakeWorktreeSource'
registerAppMessages(i18n)
let w:VueWrapper|null=null
afterEach(()=>{w?.unmount();document.body.innerHTML='';w=null})
async function setup(readOnly=false){
  i18n.global.locale.value='ko'
  const wt=createFakeWorktreeSource({status:worktreeStatus({files:Array.from({length:100},(_,i)=>worktreeFile(`src/file-${i}.ts`))})})
  const fake=createFakeGitSource({}, {worktree:wt.source})
  w=mount(DesignerGitPanel,{props:{projectId:P,flow:{groupId:G,name:'Flow'},source:fake.source,readOnly},attachTo:document.body,global:{plugins:[i18n]}})
  await flushPromises();return wt
}
describe('0068 quick execution UI',()=>{
  it('starts collapsed and filters a hundred files without any network fetch',async()=>{
    const wt=await setup()
    expect(w!.find('details').element.open).toBe(false)
    await w!.find('.gq-files').trigger('click')
    expect(w!.findAll('.gq-list label')).toHaveLength(100)
    await w!.find('.gq-pick input:not([type=checkbox])').setValue('file-99')
    expect(w!.findAll('.gq-list label')).toHaveLength(1)
    expect(w!.find('.gq-list').text()).toContain('src/file-99.ts')
    expect(wt.source.fetchRefs).not.toHaveBeenCalled()
  })
  it('runs commit scope from the panel and leaves merge and push untouched',async()=>{
    const wt=await setup()
    await w!.find('.gq-scopes button').trigger('click')
    await w!.find('.gq-row input').setValue('commit selected files')
    await w!.find('[data-test=git-execute]').trigger('click');await flushPromises()
    expect(wt.source.commit).toHaveBeenCalledTimes(1)
    expect(wt.source.manualMerge).not.toHaveBeenCalled()
    expect(wt.source.pushMergeResult).not.toHaveBeenCalled()
    expect(w!.find('.gq-result').text()).toContain('완료')
  })
  it('offers no writable execution on a read-only Flow',async()=>{
    const wt=await setup(true)
    expect(w!.find<HTMLButtonElement>('[data-test=git-execute]').element.disabled).toBe(true)
    expect(wt.source.stage).not.toHaveBeenCalled()
  })
})
