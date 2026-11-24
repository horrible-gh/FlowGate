import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { PRODUCT_NAME } from '@shared/productName'
import ko from '@shared/i18n/ko'
import en from '@shared/i18n/en'
import ja from '@shared/i18n/ja'
import appKo from '@/app/i18n/ko'
import appEn from '@/app/i18n/en'
import appJa from '@/app/i18n/ja'

// F86-7 U3: the displayed product name comes from one constant; what people see is unchanged.
describe('product name in one place', () => {
  it('keeps the displayed name byte-identical', () => {
    expect(PRODUCT_NAME).toBe('FlowGate')
    expect([ko.auth.login.title, en.auth.login.title, ja.auth.login.title]).toEqual(['FlowGate 로그인', 'FlowGate Sign In', 'FlowGate ログイン'])
  })

  it('keeps the app version, about title and sender label byte-identical', () => {
    expect([appKo.sidebar.version, appEn.sidebar.version, appJa.sidebar.version]).toEqual(['FlowGate v0.2', 'FlowGate v0.2', 'FlowGate v0.2'])
    expect(JSON.stringify([appKo, appEn, appJa])).toContain('"FlowGate 정보"')
    expect(JSON.stringify([appKo, appEn, appJa])).toContain('"About FlowGate"')
  })

  it('has no other displayed product-name literal in the files that show it', () => {
    const files = [
      'src/app/i18n/ko.ts', 'src/app/i18n/en.ts', 'src/app/i18n/ja.ts',
      'src/shared/i18n/ko.ts', 'src/shared/i18n/en.ts', 'src/shared/i18n/ja.ts',
      'src/features/documentWindow/specialDocumentModel.ts', 'src/app/components/Topbar.vue',
      'src/app/components/Sidebar.vue', 'src/login/LoginApp.vue', 'src/features/designer/top/DesignerHeader.vue',
      'scripts/third-party-notices.ts', 'index.html', 'login.html',
    ]
    for (const file of files) {
      const code = readFileSync(resolve(process.cwd(), file), 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '').replace(/<!--[\s\S]*?-->/g, '').replace(/\/\/.*$/gm, '')
      expect(code.match(/\bFlowGate\b/g), file).toBeNull()
    }
  })

  it('lets the build fill both HTML titles from the constant', () => {
    for (const file of ['index.html', 'login.html']) {
      expect(readFileSync(resolve(process.cwd(), file), 'utf8')).toContain('<title>%PRODUCT_NAME%</title>')
    }
    expect(readFileSync(resolve(process.cwd(), 'vite.config.ts'), 'utf8')).toContain("replace(/%PRODUCT_NAME%/g, PRODUCT_NAME)")
  })
})
