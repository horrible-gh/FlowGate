// Focused R5/R7 regression checks: npm run verify:web-notices (Node 24+).
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { collectNotices, packageRoot } from './third-party-notices.ts'
const read = path => readFileSync(path, 'utf8')
const root = resolve('..')
const notice = read('dist/THIRD_PARTY_NOTICES.txt')
assert.equal(read('dist/LICENSE.txt'), read('../LICENSE'))
// Integration contract: preserve exact ledger and license texts in the artifact.
assert(notice.includes(read('../THIRD_PARTY_NOTICES.md')), 'root ledger missing or stale')
const sources = JSON.parse(read('../third_party/license_sources.json'))
for (const entry of sources.files) {
  assert(notice.includes(`Project notice: ${entry.path}\n\n${read(`../${entry.path}`)}`), `missing project license: ${entry.path}`)
}
assert.throws(() => collectNotices([], resolve('node_modules')), /Required project notice ledger missing/)

// Parse generated section headers, not loose substrings inside license prose.
const packages = [...notice.matchAll(/^((?:@[^/\s]+\/)?[^@\s]+)@([^\s]+)\nInclusion: ([^\n]+)/gm)]
const emitted = packages.filter(([, , , reason]) => reason === 'emitted Rollup chunk').map(([, name]) => name).sort()
console.log('EMITTED_ROOT_PACKAGES:', JSON.stringify(emitted))
console.log('NOTICE_PACKAGES:', JSON.stringify(packages.map(([, name, version, reason]) => ({ name, version, reason }))))
assert.equal(emitted.length, 15, 'R5-1 baseline: expected 15 emitted root packages; review bundle changes before updating')
assert.equal(new Set(emitted).size, emitted.length, 'duplicate emitted package names')
// Vue's re-export entry is tree-shaken; its four implementation packages emit code.
// This baseline is the observed Rollup output, not the package.json dependency list.
assert.deepEqual(emitted, [
  '@intlify/core-base', '@intlify/message-compiler', '@intlify/shared',
  '@vue-flow/core', '@vue/reactivity', '@vue/runtime-core', '@vue/runtime-dom', '@vue/shared',
  '@xterm/addon-fit', '@xterm/xterm', 'axios', 'marked', 'pinia', 'vue-i18n', 'vue-router',
], 'emitted package set changed: review generated license sections')
for (const [, name, version] of packages) {
  assert.equal(version, JSON.parse(read(`node_modules/${name}/package.json`)).version, `installed version mismatch: ${name}`)
}
for (const name of ['@vue-flow/core', 'd3-drag', 'd3-selection', 'd3-zoom', 'd3-interpolate', 'd3-dispatch', 'd3-transition', 'd3-timer', 'd3-ease', 'd3-color']) {
  assert(notice.includes(`${name}@`), `missing ${name}`)
}
assert(notice.includes('Permission is hereby granted'))
assert(notice.includes('Redistribution and use in source and binary forms'))
assert(!notice.includes('vite@'), 'development-only package included')
assert.equal(packageRoot('\0virtual-module'), undefined)
assert.equal(packageRoot(resolve('src/main.ts')), undefined)
assert(packageRoot(resolve('node_modules/vue/dist/vue.runtime.esm-bundler.js') + '?commonjs-proxy'))
assert.throws(() => collectNotices([resolve('node_modules/missing/module.js')], root))
const licenseWindow = read('src/features/licenses/LicenseInfoWindow.vue')
assert(licenseWindow.includes('THIRD_PARTY_NOTICES.txt') && licenseWindow.includes('LICENSE.txt'), 'license window must load both notice files')
const catalog = JSON.parse(read('../server/modules/flow_gate/services/voice_catalog.json'))
const requiredLicenseKeys = ['window_title', 'close', 'tab_project', 'col_component', 'col_file', 'col_license', 'col_source', 'source_link', 'open_for', 'open', 'summary', 'pending', 'direct_download', 'project_third_party', 'project_license', 'loading', 'load_failed', 'nav_title']
for (const locale of ['ko', 'en', 'ja']) {
  const messages = (await import(`../src/app/i18n/${locale}.ts`)).default
  for (const key of ['title', 'third_party', 'project_license']) {
    assert(messages.nav.notices[key]?.trim(), `${locale}: missing legacy notice translation ${key}`)
  }
  for (const key of requiredLicenseKeys) {
    assert(messages.licenses[key]?.trim(), `${locale}: missing license translation ${key}`)
  }
  for (const service of catalog.services) {
    assert.equal(service.notice_keys.length, service.native_notices.length)
    for (const key of service.notice_keys) {
      assert(messages.licenses.notice[key]?.trim(), `${locale}: missing native notice ${key}`)
    }
    for (const item of [...service.artifacts, ...service.models]) {
      assert(messages.licenses.component_type[item.component_type]?.trim(), `${locale}: missing component type ${item.component_type}`)
    }
  }
  for (const key of ['test_upload', 'test_no_file', 'unavailable_windows']) {
    assert(messages.voice[key]?.trim(), `${locale}: missing voice translation ${key}`)
  }
}
const sidebar = read('src/app/components/Sidebar.vue')
const navigation = sidebar.match(/<nav\b[^>]*>([\s\S]*?)<\/nav>/)?.[1] ?? ''
assert(navigation.includes('gui.licenses.nav_title'), 'license entry must remain in mobile navigation')
const taskbar = read('src/features/designer/shell/DesignerTaskbar.vue')
assert(taskbar.includes(`v-if="modelessStyle === 'modern'" class="flowgate-menu-mark"`))
for (const name of ['wide1026', 'normal910', 'mid570', 'narrow392', 'min236']) {
  assert(!existsSync(`tests/visual/gitSettingsMockup/capture_${name}.png`))
}
console.log('PASS: emitted licenses, D3 closure, project LICENSE, mobile notice entry, Modern mark, removed PNGs')
