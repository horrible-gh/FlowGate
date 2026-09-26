import { beforeEach, describe, expect, it } from 'vitest'
import {
  clearStoredCurrentProjectId,
  readStoredCurrentProjectId,
  writeStoredCurrentProjectId,
} from '@/features/project/currentProjectStorage'

const CURRENT_KEY = 'fg_current_project_id'
const BRANCH_KEY = 'fg_current_branch'

beforeEach(() => {
  localStorage.clear()
})

describe('currentProjectStorage', () => {
  it('reads null when nothing is stored', () => {
    expect(readStoredCurrentProjectId()).toBeNull()
  })

  it('round-trips a stored project id under the v0.1-compatible key', () => {
    writeStoredCurrentProjectId('demo')
    expect(localStorage.getItem(CURRENT_KEY)).toBe('demo')
    expect(readStoredCurrentProjectId()).toBe('demo')
  })

  it('clears the stored value', () => {
    writeStoredCurrentProjectId('demo')
    clearStoredCurrentProjectId()
    expect(readStoredCurrentProjectId()).toBeNull()
    expect(localStorage.getItem(CURRENT_KEY)).toBeNull()
  })

  it('never reads or writes the Git branch storage key', () => {
    localStorage.setItem(BRANCH_KEY, 'feature/untouched')
    writeStoredCurrentProjectId('demo')
    clearStoredCurrentProjectId()
    expect(localStorage.getItem(BRANCH_KEY)).toBe('feature/untouched')
  })
})
