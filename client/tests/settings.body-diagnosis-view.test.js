// flowgate.v02.0064 T-B1d (P0006 §6.3) — the project settings tab "문서 본문 진단": diagnose a Flow or the
// project, recover the selected recoverable documents with their diagnosed methods (50 per request), diagnose
// again, and never count a refused or failed document as recovered. A response that arrives after the project
// changed is the old project's and is dropped (no rows, no selection, no recovery request).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { createI18n } from 'vue-i18n';
import ko from '@shared/i18n/ko';
import en from '@shared/i18n/en';
import ja from '@shared/i18n/ja';
import BodyDiagnosisView from '../src/settings/views/project/BodyDiagnosisView.vue';

const { getRequest, postRequest, showToast, store } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  showToast: vi.fn(),
  store: { current: null },
}));

vi.mock('@shared/api', () => ({ getRequest, postRequest }));
vi.mock('../src/settings/stores/settings.js', async () => {
  const { reactive } = await import('vue');
  store.current = reactive({ currentProjectId: 'test2' });
  return { useSettingsStore: () => store.current };
});
vi.mock('../src/main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }));

const i18n = createI18n({ legacy: false, locale: 'ko', messages: { ko, en, ja } });
const FLOW = 'test2.default.0052';
const OTHER_FLOW = 'other.m.0001';
const DIAGNOSIS_URL = '/api/v1/documents/body-diagnosis';

const item = (seq, over = {}) => ({
  doc_id: `${FLOW}.${String(seq).padStart(4, '0')}-D`, code: `${String(seq).padStart(4, '0')}-D`, title: `D ${seq}`,
  type: 'D', status: 'draft', file_path: '', reason: 'file_path_missing', method: 'regenerate_stub', recoverable: true,
  body_loss: 'lost', origin: { card: false }, candidate: null, evidence: null, ...over,
});
const diagnosis = (items, fingerprint = 'a'.repeat(64), extra = {}) => ({
  ok: true, scope: { kind: 'group', id: FLOW, project_id: 'test2' },
  storage_root: { effective: 'D:/flowgate/storage', source: 'project_setting', configured: {} },
  summary: { checked: 10, unreadable: items.length, recoverable: items.filter((i) => i.recoverable).length,
    storage_root_suspect: 0, suspect_roots: [], ...extra },
  items, fingerprint,
});

function routes(diagnoses) {
  const queue = [...diagnoses];
  getRequest.mockImplementation(async (url) => {
    if (url === '/api/v1/groups') return { data: { groups: [{ group_id: FLOW, title: '접수' }] } };
    return { data: queue.length > 1 ? queue.shift() : queue[0] };
  });
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

const mounted = [];

async function view() {
  const wrapper = mount(BodyDiagnosisView, { global: { plugins: [i18n] } });
  mounted.push(wrapper);
  await flushPromises();
  return wrapper;
}

const flowOptions = (wrapper) => wrapper.findAll('[data-testid="scope-flow"] option').map((o) => o.attributes('value'));
const diagnosisCalls = () => getRequest.mock.calls.filter(([url]) => url === DIAGNOSIS_URL).length;

beforeEach(() => {
  store.current.currentProjectId = 'test2';
  getRequest.mockReset();
  postRequest.mockReset();
  showToast.mockReset();
});

afterEach(() => {
  mounted.splice(0).forEach((wrapper) => wrapper.unmount());
});

describe('BodyDiagnosisView', () => {
  it('diagnoses the chosen Flow or the project and lists reasons, methods and loss', async () => {
    routes([diagnosis([item(1), item(2, { reason: 'read_error', method: 'read_error_manual', recoverable: false, body_loss: null })],
      'a'.repeat(64), { storage_root_suspect: 1, suspect_roots: ['D:/old'] })]);
    const wrapper = await view();
    expect(wrapper.find('[data-testid="run-diagnosis"]').attributes('disabled')).toBeDefined();
    await wrapper.find('[data-testid="scope-flow"]').setValue(FLOW);
    await wrapper.find('[data-testid="run-diagnosis"]').trigger('click');
    await flushPromises();
    expect(getRequest).toHaveBeenCalledWith('/api/v1/documents/body-diagnosis', { group_id: FLOW });
    const rows = wrapper.findAll('tbody tr');
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain(i18n.global.t('settings.project.body_diagnosis.method.regenerate_stub'));
    expect(rows[0].text()).toContain(i18n.global.t('settings.project.body_diagnosis.loss.lost'));
    expect(rows[1].find('input').attributes('disabled')).toBeDefined();
    expect(wrapper.find('[data-testid="diagnosis-suspect"]').text()).toContain('D:/old');
    await wrapper.find('[data-testid="scope-project"]').setValue();
    await wrapper.find('[data-testid="run-diagnosis"]').trigger('click');
    await flushPromises();
    expect(getRequest).toHaveBeenLastCalledWith('/api/v1/documents/body-diagnosis', { project_id: 'test2' });
  });

  it('recovers the selection 50 at a time, shows each result and diagnoses again', async () => {
    const items = Array.from({ length: 51 }, (_, i) => item(i + 1));
    routes([diagnosis(items), diagnosis([items[0]], 'b'.repeat(64))]);
    postRequest.mockImplementation(async (_url, body) => ({
      data: { ok: true, results: body.selections.map((s) => ({ doc_id: s.doc_id,
        status: s.doc_id === items[0].doc_id ? 'refused' : 'recovered', body_loss: 'lost',
        refusal: s.doc_id === items[0].doc_id ? { error: 'target_in_use', detail: '…', evidence: {} } : null })) },
    }));
    const wrapper = await view();
    await wrapper.find('[data-testid="scope-flow"]').setValue(FLOW);
    await wrapper.find('[data-testid="run-diagnosis"]').trigger('click');
    await flushPromises();
    await wrapper.find('[data-testid="select-all"]').setValue(true);
    await wrapper.find('[data-testid="recover-selected"]').trigger('click');
    await flushPromises();
    expect(postRequest).toHaveBeenCalledTimes(2);
    expect(postRequest.mock.calls[0][1].selections).toHaveLength(50);
    expect(postRequest.mock.calls[0][1].selections[0]).toEqual({ doc_id: items[0].doc_id, expected_method: 'regenerate_stub' });
    expect(postRequest.mock.calls[1][1].selections).toHaveLength(1);
    const outcome = wrapper.find('[data-testid="recovery-outcome"]');
    expect(outcome.text()).toContain(i18n.global.t('settings.project.body_diagnosis.result_summary', { recovered: 50, refused: 1, failed: 0 }));
    expect(outcome.find('[data-status="refused"]').text()).toContain(i18n.global.t('settings.project.body_diagnosis.refusal.target_in_use'));
    const diagnoses = getRequest.mock.calls.filter(([url]) => url === '/api/v1/documents/body-diagnosis');
    expect(diagnoses).toHaveLength(2);
    expect(wrapper.findAll('tbody tr')).toHaveLength(1);
    expect(wrapper.find('[data-testid="diagnosis-changed"]').exists()).toBe(true);
  });

  it('drops a diagnosis that answers after the project changed', async () => {
    const late = deferred();
    const otherItem = item(7, { doc_id: `${OTHER_FLOW}.0007-D`, code: '0007-D' });
    getRequest.mockImplementation(async (url, params) => {
      if (url === '/api/v1/groups') {
        return { data: { groups: [{ group_id: params.project_id === 'test2' ? FLOW : OTHER_FLOW }] } };
      }
      if (params.group_id === FLOW) return late.promise;
      return { data: { ...diagnosis([otherItem]), scope: { kind: 'group', id: OTHER_FLOW, project_id: 'other' } } };
    });
    const wrapper = await view();
    await wrapper.find('[data-testid="scope-flow"]').setValue(FLOW);
    await wrapper.find('[data-testid="run-diagnosis"]').trigger('click');
    store.current.currentProjectId = 'other';
    await flushPromises();
    late.resolve({ data: diagnosis([item(1), item(2)]) });              // project test2's answer, late
    await flushPromises();
    expect(wrapper.find('[data-testid="diagnosis-table"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="recover-selected"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="run-diagnosis"]').text()).toBe(i18n.global.t('settings.project.body_diagnosis.run'));
    expect(flowOptions(wrapper)).toEqual(['', OTHER_FLOW]);
    await wrapper.vm.recoverSelected();
    expect(postRequest).not.toHaveBeenCalled();
    await wrapper.find('[data-testid="scope-flow"]').setValue(OTHER_FLOW);
    await wrapper.find('[data-testid="run-diagnosis"]').trigger('click');
    await flushPromises();
    expect(wrapper.findAll('tbody tr').map((row) => row.attributes('data-doc'))).toEqual([otherItem.doc_id]);
    postRequest.mockResolvedValue({ data: { ok: true, results: [] } });
    await wrapper.find('[data-testid="select-all"]').setValue(true);
    await wrapper.find('[data-testid="recover-selected"]').trigger('click');
    await flushPromises();
    expect(postRequest.mock.calls.flatMap(([, body]) => body.selections.map((s) => s.doc_id))).toEqual([otherItem.doc_id]);
  });

  it('drops a recovery that answers after the project changed, with its remaining requests and re-diagnosis', async () => {
    const items = Array.from({ length: 51 }, (_, i) => item(i + 1));
    routes([diagnosis(items)]);
    const late = deferred();
    postRequest.mockImplementation(() => late.promise);
    const wrapper = await view();
    await wrapper.find('[data-testid="scope-flow"]').setValue(FLOW);
    await wrapper.find('[data-testid="run-diagnosis"]').trigger('click');
    await flushPromises();
    await wrapper.find('[data-testid="select-all"]').setValue(true);
    await wrapper.find('[data-testid="recover-selected"]').trigger('click');
    store.current.currentProjectId = 'other';
    await flushPromises();
    const diagnosed = diagnosisCalls();
    late.resolve({ data: { ok: true, results: items.slice(0, 50).map((i) => ({
      doc_id: i.doc_id, status: 'recovered', body_loss: 'lost', refusal: null })) } });
    await flushPromises();
    expect(postRequest).toHaveBeenCalledTimes(1);                       // the old project's next 50 never go
    expect(diagnosisCalls()).toBe(diagnosed);                           // nor its re-diagnosis
    expect(wrapper.find('[data-testid="recovery-outcome"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="diagnosis-table"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="run-diagnosis"]').text()).toBe(i18n.global.t('settings.project.body_diagnosis.run'));
  });

  it('keeps the new project\'s Flow list when the old one answers late', async () => {
    const late = deferred();
    getRequest.mockImplementation((_url, params) => (params.project_id === 'test2'
      ? late.promise : Promise.resolve({ data: { groups: [{ group_id: OTHER_FLOW }] } })));
    const wrapper = await view();
    store.current.currentProjectId = 'other';
    await flushPromises();
    late.resolve({ data: { groups: [{ group_id: FLOW }] } });
    await flushPromises();
    expect(flowOptions(wrapper)).toEqual(['', OTHER_FLOW]);
  });

  it('has the tab strings in ko, en and ja', () => {
    const keys = (tree, prefix = '') => Object.entries(tree).flatMap(([key, value]) =>
      (value && typeof value === 'object' ? keys(value, `${prefix}${key}.`) : [`${prefix}${key}`]));
    const of = (messages) => keys(messages.settings.project.body_diagnosis).sort();
    expect(of(en)).toEqual(of(ko));
    expect(of(ja)).toEqual(of(ko));
  });
});
