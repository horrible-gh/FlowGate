import { defineConfig, mergeConfig } from 'vitest/config'
import base from '../vitest.config'

const files: unknown = JSON.parse(process.env.FG_TEST_AREA_FILES ?? 'null')
if (!Array.isArray(files) || !files.length || files.some(file =>
  typeof file !== 'string' || !file.startsWith('tests/') || !file.endsWith('.spec.ts')
  || file.split('/').includes('..') || /[\\*?\[\]{}]/.test(file))) {
  throw new Error('Use npm run test:area -- <area> to supply exact test files')
}

// Override include rather than CLI substring filters (foo.spec.ts can match another path).
const config = mergeConfig(base, defineConfig({}))
config.test = { ...config.test, include: files as string[] }
export default config
