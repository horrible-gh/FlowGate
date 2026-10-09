/**
 * flowgate.v02.0048 T#3 — the features/document public surface (D#1 §2.3 / §6.2 / §6.3). The
 * index re-exports every api file and the common error file, and each export name starts with
 * its file's area so two files never clash. Data driven over the files, so a feature set filling
 * its own api file never edits this spec.
 */
import { readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import * as DocumentFeature from '@/features/document'

const API_DIR = resolve(__dirname, '../../../src/features/document/api')
const API_FILES = readdirSync(API_DIR).filter((name) => name.endsWith('.ts')).map((name) => name.replace(/\.ts$/, '')).sort()
const modules = import.meta.glob('../../../src/features/document/api/*.ts', { eager: true }) as Record<string, Record<string, unknown>>
const errorModule = (await import('@/features/document/documentError')) as Record<string, unknown>

describe('features/document public surface (flowgate.v02.0048)', () => {
  it('has the api file of every area of D#1 §6.3 (an extension may add more, D#1 §11.1)', () => {
    expect(API_FILES).toEqual(
      expect.arrayContaining(['attachments', 'check', 'document', 'kindSwitch', 'qa', 'review', 'settings', 'testSpec', 'workPlan']),
    )
  })

  it('re-exports every api file and the common error file, nothing else', () => {
    const expected = [...Object.values(modules).flatMap((module) => Object.keys(module)), ...Object.keys(errorModule)].sort()
    expect(Object.keys(DocumentFeature).sort()).toEqual(expected)
    expect(Object.keys(DocumentFeature)).toEqual(expect.arrayContaining(['documentRead', 'toDocumentRecord', 'documentErrorOf']))
  })

  it.each(API_FILES)('every export of api/%s starts with its area', (area) => {
    const module = modules[`../../../src/features/document/api/${area}.ts`]
    // A normalizer may read `to<Area>…` (toDocumentRecord).
    const prefix = new RegExp(`^(to)?${area}`, 'i')
    for (const name of Object.keys(module)) expect(name, `${area}.ts: ${name}`).toMatch(prefix)
  })

  it('the common error file exports start with `document`', () => {
    for (const name of Object.keys(errorModule)) expect(name).toMatch(/^document/)
  })
})
