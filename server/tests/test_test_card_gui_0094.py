"""0094: Chromium drives the real card; authoring/run HTTP handlers and the Case's own command execute.
No test framework is assumed: a JavaScript case runs with `node --test`, a Python case as a plain script.
Only authentication, storage and the source Basis are isolated using the existing 0549 fixture.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest
from test_test_spec_0549 import env, TS_ID, _basis_for_test, _measured


TARGETS = {
    'js': ('tests/gui_case.test.mjs', 'node --test {target}',
           "import {{ test }} from 'node:test'\nimport assert from 'node:assert/strict'\n"
           "test('saved case', () => {{ assert.equal(2 + 2, {answer}) }})\n"),
    'py': ('tests/gui_case_check.py', 'python {target}', 'assert 2 + 2 == {answer}\nprint("checked")\n'),
}


@pytest.mark.parametrize("language,answer,status", [("js", 4, "PASS"), ("js", 5, "FAIL"), ("py", 4, "PASS")])
def test_gui_save_run_and_read_report(env, monkeypatch, tmp_path, language, answer, status):
    from playwright.sync_api import sync_playwright, expect
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.documents.routers import documents
    from modules.flow_gate.api.v1 import test_run_routes
    from modules.flow_gate.services import test_spec_service as spec, test_basis_service as basis

    root = Path(__file__).resolve().parents[2]
    gui = root / 'client_gui'
    work = tmp_path / 'execution'
    (work / 'tests').mkdir(parents=True)
    (work / 'scratch').mkdir()
    target, command, source = TARGETS[language]
    (work / target).write_text(source.format(answer=answer), encoding='utf-8')
    measured = _basis_for_test()
    _measured(env, monkeypatch, measured, work)
    monkeypatch.setattr(basis, 'resolve', lambda *a, **k: measured)
    monkeypatch.setattr(basis, 'source_root', lambda *a, **k: work)
    env.docs[TS_ID]['doc_review_status'] = 'draft'
    env.contents[TS_ID] = '---\ntest_contract_version: 2\n---\n# GUI test\n\nInstruction\n\n## 시험 사양\n\n## Notes\nKeep this note  \n'
    monkeypatch.setattr(documents.document_service, 'get_document', lambda doc: env.docs.get(doc))
    monkeypatch.setattr(test_run_routes, 'verify_bearer', lambda req: {'_is_user_jwt': True, 'issued_to': 'u', 'is_admin': False})
    monkeypatch.setattr(env.svc, 'user_can_run_tests', lambda *a: True)
    def persist(doc_id, body, user, request=None):
        env.contents[doc_id] = body.content
        return {'ok': True}
    monkeypatch.setattr(documents, 'update_document_content', persist)
    app = FastAPI()
    app.include_router(documents.router)
    app.include_router(test_run_routes.router)
    app.dependency_overrides[documents.get_current_user] = lambda: {'user_id': 'u'}
    # Optional browser cache next to --basetemp keeps self-check dependencies in scratch.
    browser_cache = tmp_path.parent.parent / 'browsers'
    if browser_cache.is_dir():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(browser_cache))
    monkeypatch.setenv('TEMP', str(tmp_path))
    monkeypatch.setenv('TMP', str(tmp_path))
    calls = []
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    log = (tmp_path / 'vite.log').open('w', encoding='utf-8')
    process = subprocess.Popen([shutil.which('node'), str(gui / 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', str(port), '--strictPort'], cwd=gui, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        origin = f'http://127.0.0.1:{port}'
        for _ in range(100):
            try:
                urlopen(origin + '/app/', timeout=1).close()
                break
            except Exception:
                if process.poll() is not None:
                    pytest.fail('Vite failed: ' + (tmp_path / 'vite.log').read_text(encoding='utf-8'))
                time.sleep(.1)
        with TestClient(app) as client, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, timeout=20000)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            def route(request_route):
                req = request_route.request
                path = urlsplit(req.url).path
                suffix = path.split('/api/v1', 1)[-1]
                calls.append((req.method, suffix))
                if req.method == 'OPTIONS':
                    request_route.fulfill(status=204, headers={'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*'})
                    return
                if suffix.endswith('/test-document'):
                    data = env.svc.describe_test_document(env.docs[TS_ID]); status = 200
                elif suffix.endswith('/test-spec') or suffix.endswith('/test-brief') or suffix.endswith('/test-spec/runs'):
                    prefix = test_run_routes.router.prefix if suffix.endswith('/runs') else documents.router.prefix
                    response = client.request(req.method, prefix + suffix.removeprefix('/documents') if prefix.endswith('/documents') else prefix + suffix,
                        json=req.post_data_json if req.post_data else None)
                    status = response.status_code; data = response.json()
                    if suffix.endswith('/runs') and status == 202:
                        env.svc.execute_run(env.db_test_runs.get_run(data['run_id']))
                elif '/settings' in suffix:
                    data = {'flow_id': 'flowgate.v02.0049', 'node_key': 'gui-test', 'card_kind': 'test', 'locked': False,
                            'fields': {'instruction': {'value': 'use', 'choices': ['use', 'omit']}}}; status = 200
                elif suffix.endswith('/history'):
                    data = {'doc_id': TS_ID, 'ai_review_history': [], 'rejection_history': []}; status = 200
                elif '/activity' in suffix:
                    data = {'active': False}; status = 200
                else:
                    data = {**env.docs[TS_ID], 'is_editable': True, 'group_disposed': False, 'active': False}; status = 200
                request_route.fulfill(status=status, content_type='application/json', body=json.dumps(data), headers={'access-control-allow-origin': '*'})
            page.route('**/api/v1/**', route)
            page.goto(origin + '/app/tests/browser/testCard0094.html?doc=' + TS_ID)
            expect(page.locator('[data-test="inspector-test-spec"]')).to_have_attribute('data-status', 'ready')
            page.locator('[data-test="test-card-brief"]').click()
            expect(page.locator('[data-test="brief-cancel"]')).to_have_count(0)
            for locale in ('ko', 'en', 'ja'):
                page.evaluate("locale => window.testI18n.global.locale.value = locale", locale)
                page.locator('[data-test="brief-close"]').click()
                page.evaluate("Object.assign(window.testCard.value.windows.brief.geometry, {width:360, height:420, x:100, y:160})")
                page.locator('[data-test="test-card-brief"]').click()
                panel = page.locator('.ui-modeless-panel').bounding_box()
                assert panel['width'] == 360 and panel['x'] + panel['width'] <= 1440
                for control in ('brief-save', 'brief-close'):
                    box = page.locator(f'[data-test="{control}"]').bounding_box()
                    assert box['x'] >= panel['x'] and box['x'] + box['width'] <= panel['x'] + panel['width'] + 1
                page.screenshot(path=str(tmp_path / f'brief-{locale}.png'))
            page.evaluate("window.testI18n.global.locale.value = 'ko'")
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.locator('[data-test="brief-text"]').fill('Saved from Chromium')
            page.locator('[data-test="brief-save"]').click()
            expect(page.locator('[data-test="brief-window"]')).to_have_count(0)
            assert spec.brief_parts(env.contents[TS_ID])[1] == 'Saved from Chromium'
            page.evaluate("window.testCard.value.openWindow('scenario')")
            expect(page.locator('.ui-modeless-title')).to_have_text('시나리오')
            page.locator('[data-test="scenario-add"]').click()
            for field, value in {'title': 'Browser case', 'requirement': 'R1', 'procedure': 'Execute assertion', 'expected': 'Pass', 'code': '6'}.items():
                page.locator(f'[data-test="case-{field}"]').fill(value)
            expect(page.locator('[data-test="case-required"] [aria-checked="true"]')).to_have_text('무시하고 진행')
            expect(page.locator('[data-test="case-rewrite"]')).to_have_count(0)
            page.locator('[data-test="case-save"]').click()
            expect(page.locator('[data-test="case-window"]')).to_have_count(0)
            parsed = spec.parse_spec(env.contents[TS_ID])
            assert not parsed['errors'] and parsed['cases'][0]['required'] is False
            assert 'Keep this note  ' in env.contents[TS_ID]
            page.locator('[data-test="scenario-edit"]').click()
            expect(page.locator('[data-test="case-title"]')).to_have_value('Browser case')
            expect(page.locator('[data-test="case-required"] [aria-checked="true"]')).to_have_text('무시하고 진행')
            expect(page.locator('[data-test="test-card-report-summary"]')).to_have_count(0)
            expect(page.locator('[data-test="test-spec-run"]')).to_be_disabled()
            expect(page.locator('[data-test="test-spec-run"]')).to_have_attribute('title', '케이스에 실행 대상 테스트 파일을 연결해 주세요.')
            assert 'pytest' not in page.locator('[data-test="case-window"]').inner_text()
            expect(page.locator('[data-test="case-conflict"]')).to_have_count(0)
            expect(page.locator('[data-test="case-reload"]')).to_have_count(0)
            page.locator('[data-test="case-title"]').fill('Unsaved draft')
            env.contents[TS_ID] = env.contents[TS_ID].replace('Browser case', 'Changed elsewhere')
            page.evaluate("window.testCard.value.reload()")
            expect(page.locator('[data-test="case-conflict"]')).to_have_text('내용이 변경되었습니다')
            assert page.locator('[data-test="case-window"]').inner_text().count('내용이 변경되었습니다') == 1
            expect(page.locator('[data-test="case-save"]')).to_be_disabled()
            expect(page.locator('[data-test="case-title"]')).to_have_value('Unsaved draft')
            page.locator('[data-test="case-reload"]').click()
            expect(page.locator('[data-test="case-title"]')).to_have_value('Changed elsewhere')
            expect(page.locator('[data-test="case-conflict"]')).to_have_count(0)
            page.locator('[data-test="case-title"]').fill('Browser case')
            page.locator('[data-test="case-code"]').fill(target)
            page.locator('[data-test="case-save"]').click()
            expect(page.locator('[data-test="case-window"]')).to_have_count(0)
            if language == 'js':
                # A test file without its command is not run as if a framework were implied.
                expect(page.locator('[data-test="test-spec-run"]')).to_be_disabled()
                expect(page.locator('[data-test="test-spec-run"]')).to_have_attribute('title', '케이스에 실행 명령을 입력해 주세요.')
            page.locator('[data-test="scenario-edit"]').click()
            page.locator('[data-test="case-command"]').fill('bash -c "exit 0"')
            page.locator('[data-test="case-save"]').click()
            expect(page.locator('[data-test="case-window"]')).to_have_count(0)
            expect(page.locator('[data-test="test-spec-run"]')).to_have_attribute('title', '허용되지 않는 실행 명령입니다.')
            page.locator('[data-test="scenario-edit"]').click()
            page.locator('[data-test="case-command"]').fill(command)
            page.locator('[data-test="case-save"]').click()
            expect(page.locator('[data-test="case-window"]')).to_have_count(0)
            saved = spec.parse_spec(env.contents[TS_ID])['cases'][0]
            assert (saved['automation_ref'], saved['run_command'], saved['run_cwd']) == (target, command, '')
            page.locator('[data-test="scenario-window"]').locator('xpath=ancestor::*[contains(@class,"ui-modeless-panel")]').locator('[data-modeless-action="close"]').click()
            expect(page.locator('[data-test="test-spec-run"]')).to_be_enabled()
            page.locator('[data-test="test-spec-run"]').click()
            expect(page.locator('[data-test="test-spec-state"]')).to_have_attribute('data-state', 'done', timeout=60000)
            page.evaluate("window.testCard.value.openWindow('report')")
            expect(page.locator('[data-test="report-row"]')).to_contain_text('Browser case')
            expect(page.locator('[data-test="report-cell"]')).to_have_attribute('data-state', status.lower())
            page.locator('[data-test="report-row"]').first.click()
            expect(page.locator('[data-test="report-command"]')).to_have_text(command)
            rows = env.db_test_runs.list_cases(env.db_test_runs.latest_by_doc(TS_ID)['run_id'])
            assert rows[0]['case_status'] == status
            evidence = json.dumps(json.loads(rows[0]['case_meta'])['evidence'], ensure_ascii=False)
            assert command.split()[0] in evidence and 'pytest' not in evidence
            assert not any('review_transitions' in path for _, path in calls)
            assert not any(t.get('action') == 'approve' and t.get('doc_id') == TS_ID for t in env.transitions)
            assert not errors, errors
            page.screenshot(path=str(tmp_path / 'report.png'), full_page=True)
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=15)
        log.close()
