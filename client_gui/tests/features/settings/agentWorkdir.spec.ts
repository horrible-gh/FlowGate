import { describe, expect, it } from 'vitest'
import { composeWorkdir, jobFolderPreview, workdirParent } from '@/features/settings/agentWorkdir'

// flowgate.v02test.0043 CH0004: the job folder location input takes the parent folder only.
describe('agentWorkdir', () => {
  it.each([
    ['/data/flowgate', '/data/flowgate/${AGENT_JOB_ID}'],
    ['  /data/flowgate//  ', '/data/flowgate/${AGENT_JOB_ID}'],
    ['/', '/${AGENT_JOB_ID}'],
    ['C:\\flowgate', 'C:\\flowgate\\${AGENT_JOB_ID}'],
    ['C:\\', 'C:\\${AGENT_JOB_ID}'],
    ['C:', 'C:\\${AGENT_JOB_ID}'],
    ['\\\\nas\\fg', '\\\\nas\\fg\\${AGENT_JOB_ID}'],
    ['C:/fg', 'C:/fg/${AGENT_JOB_ID}'],
    ['jobs', 'jobs/${AGENT_JOB_ID}'],
    ['/srv/${AGENT_JOB_ID}', '/srv/${AGENT_JOB_ID}'],
    ['C:\\fg\\${AGENT_JOB_ID}\\w', 'C:\\fg\\${AGENT_JOB_ID}\\w'],
  ])('composeWorkdir(%j) = %j', (typed, stored) => {
    expect(composeWorkdir(typed)).toBe(stored)
  })

  it('an empty input clears the setting', () => {
    expect(composeWorkdir('')).toBeNull()
    expect(composeWorkdir('   ')).toBeNull()
  })

  it.each([
    [null, ''],
    ['', ''],
    ['/data/flowgate/${AGENT_JOB_ID}', '/data/flowgate'],
    ['/${AGENT_JOB_ID}', '/'],
    ['C:\\fg\\${AGENT_JOB_ID}', 'C:\\fg'],
    ['C:\\${AGENT_JOB_ID}', 'C:\\'],
    ['C:\\fg\\${AGENT_JOB_ID}\\w', 'C:\\fg\\${AGENT_JOB_ID}\\w'],
    ['${AGENT_JOB_ID}', '${AGENT_JOB_ID}'],
    ['/a/${AGENT_JOB_ID}/${AGENT_JOB_ID}', '/a/${AGENT_JOB_ID}/${AGENT_JOB_ID}'],
    ['/legacy/fixed', '/legacy/fixed'],
  ])('workdirParent(%j) = %j and stores the same value again', (stored, shown) => {
    expect(workdirParent(stored)).toBe(shown)
    if (stored && stored.includes('${AGENT_JOB_ID}')) expect(composeWorkdir(shown)).toBe(stored)
  })

  it('previews the folder a Job gets', () => {
    expect(jobFolderPreview(composeWorkdir('/data/flowgate')!)).toBe('/data/flowgate/job_…')
    expect(jobFolderPreview('C:\\fg\\${AGENT_JOB_ID}\\w')).toBe('C:\\fg\\job_…\\w')
  })
})
