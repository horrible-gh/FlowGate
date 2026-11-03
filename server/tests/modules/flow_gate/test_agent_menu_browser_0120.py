"""Opt-in browser check of the actual SPA, router and Agent API against isolated SQLite.

FG_AGENT_BROWSER_ARTIFACTS points to scratch. No service writes or Agent runtime
enrollment/heartbeat calls; issued enrollment tokens are masked in screenshots.
"""
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from test_system_agents_0076 import env, _create, LOCAL_BODY, REMOTE_BODY, API  # noqa: F401


@pytest.mark.skipif(not os.environ.get('FG_AGENT_BROWSER_ARTIFACTS'), reason='opt-in browser check needs scratch artifacts')
def test_agent_menu_real_router_save_refresh_and_permissions(env):
    from playwright.sync_api import sync_playwright, expect
    scratch = Path(os.environ['FG_AGENT_BROWSER_ARTIFACTS']).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[4]
    client = repo / 'client_gui'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    aliases = {'@': str(client/'src'), '@shared': str(repo/'client/shared')}
    aliases.update({name: str(client/'node_modules'/name) for name in ['vue', 'vue-i18n', 'axios']})
    config = scratch/'browser-vite.config.mjs'
    config.write_text('import vue from '+json.dumps((client/'node_modules/@vitejs/plugin-vue/dist/index.mjs').as_uri())+';\nexport default '+json.dumps({
        'root': str(client), 'base': '/app/', 'cacheDir': str(scratch/'vite-cache'),
        'resolve': {'preserveSymlinks': True, 'alias': aliases},
        'define': {'import.meta.env.VITE_API_BASE_URL': json.dumps('/flowgate'), '__FG_GUI_BUILD__': json.dumps('isolated-0120')},
        'server': {'host': '127.0.0.1', 'port': port, 'strictPort': True, 'fs': {'allow': [str(repo), str(scratch)]}},
    })[:-1]+', plugins: [vue()] };', encoding='utf-8')
    log = (scratch/'vite.log').open('w', encoding='utf-8')
    process = subprocess.Popen([shutil.which('node'), str(client/'node_modules/vite/bin/vite.js'), '--config', str(config)],
        cwd=client, env={**os.environ, 'TEMP':str(scratch), 'TMP':str(scratch)}, stdout=log, stderr=log,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    origin = f'http://127.0.0.1:{port}'
    url = origin+'/app/settings/system/agents'
    evidence = {'url': url, 'fixture': 'actual SPA + isolated migrated SQLite + real auth/RBAC/API; TestClient transport', 'requests': [], 'screens': []}
    local = _create(env, {**LOCAL_BODY, 'name': 'Existing local', 'enabled': False,
        'configured_capabilities': {'ai_cli': True, 'ai_api': False, 'storage': True}})
    remote = _create(env, {**REMOTE_BODY, 'name': 'Existing remote'})
    local_id, remote_id = local['agent_id'], remote['agent_id']
    # Pending enrollment is an actual 'Needs attention' state; no machine is simulated.
    assert env.client.post(f'{API}/system/agents/{remote_id}/enrollments', json={}, headers=env.h('A')).status_code == 201
    remote = env.agent(remote_id).json()
    before = env.agent(local_id).json()
    def bridge(route):
        req = route.request
        parsed = urlsplit(req.url)
        # Vite /@fs URLs may themselves contain a directory named flowgate.
        if not parsed.path.startswith('/flowgate/'):
            route.continue_()
            return
        path = parsed.path+('?' + parsed.query if parsed.query else '')
        assert '/api/v1/agent/' not in path, 'Browser must never simulate a machine connection'
        response = env.client.request(req.method, path, content=req.post_data,
            headers={'Authorization': req.header_value('authorization') or '', 'Content-Type': 'application/json', 'X-Locale': 'ko'})
        evidence['requests'].append({'method': req.method, 'path': path, 'status': response.status_code})
        route.fulfill(status=response.status_code, content_type='application/json', body=response.content)
    def capture(page, name):
        page.screenshot(path=str(scratch/(name+'.png')), full_page=True,
            mask=[page.locator('[data-testid="enrollment-token"]')])
        evidence['screens'].append({'file': name+'.png', 'url': page.url})
    def context_for(browser, who):
        context = browser.new_context(viewport={'width':1440, 'height':1000}, locale='ko-KR')
        context.add_init_script('window.__accessToken__ = '+json.dumps(env.tokens[who])+';')
        context.route('**/flowgate/**', bridge)
        return context
    try:
        for _ in range(100):
            assert process.poll() is None, 'Vite stopped; see scratch/vite.log'
            try:
                urllib.request.urlopen(url, timeout=1).close()
                break
            except (OSError, TimeoutError): time.sleep(.1)
        else: pytest.fail('Vite did not start')
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = context_for(browser, 'A')
                page = context.new_page()
                page.goto(url)
                expect(page.locator('.agent-row')).to_have_count(2)
                expect(page.locator('.scope.open [data-scope]')).to_have_attribute('data-scope', 'me')
                expect(page.locator('[data-subnav="agents"]')).to_have_text('에이전트')
                expect(page.locator('[data-subnav="agents"]')).to_have_attribute('href', '/app/settings/system/agents')
                capture(page, 'agents-direct')
                page.locator('.nav-home').click()
                expect(page.locator('[data-scope-card="me"]')).to_contain_text('에이전트')
                expect(page.locator('[data-scope-card="system"]')).not_to_contain_text('에이전트')
                page.locator(f'[data-attention="{remote_id}"] [data-testid="hub-attention-go"]').first.click()
                expect(page.locator('[data-testid="agent-detail"]')).to_have_attribute('data-agent', remote_id)
                # The existing view consumes ?agent after opening the matching record.
                expect(page).to_have_url(url)
                evidence['hub_query_consumed'] = True
                capture(page, 'agents-hub-query')
                page.keyboard.press('Escape')
                page.locator('[data-subnav="agents"]').click()
                expect(page.locator('[data-subnav="agents"]')).to_have_attribute('aria-current', 'page')
                capture(page, 'agents-menu')
                page.locator('[data-testid="agent-new"]').click()
                expect(page.locator('[data-testid="agent-name"]')).to_be_focused()
                box = page.locator('.agent-settings-dialog').bounding_box()
                assert box and abs(box['width']-515) < 2
                evidence['modal_box'] = box
                capture(page, 'agents-register')
                page.locator('[data-testid="agent-name"]').fill('Browser registered')
                page.locator('[data-testid="agent-create-submit"]').click()
                expect(page.locator('[data-testid="enrollment-token"]')).to_be_visible()
                token = page.locator('[data-testid="enrollment-token"]').inner_text()
                assert len(token) > 20
                listing = env.client.get(API+'/system/agents', headers=env.h('A')).json()['agents']
                created = next(a for a in listing if a['name']=='Browser registered')
                aid = created['agent_id']
                assert created['enrollment_status']=='pending' and created['online_status']=='offline'
                assert not created['has_registration_history']
                evidence['created'] = {k:created[k] for k in ['agent_id', 'name', 'online_status', 'enrollment_status']}
                issued = [r for r in evidence['requests'] if r['method']=='POST' and r['path'].endswith('/enrollments')]
                assert issued == [{'method':'POST', 'path':f'{API}/system/agents/{aid}/enrollments', 'status':201}]
                capture(page, 'agents-issued-masked')
                page.keyboard.press('Escape')
                expect(page.locator('[data-testid="enrollment-token"]')).to_have_count(0)
                assert token not in page.content()
                assert page.evaluate('(s)=>![localStorage,sessionStorage].some(x=>Object.values(x).some(v=>v.includes(s)))', token)
                edit = page.locator(f'[data-edit-agent="{local_id}"]')
                edit.focus();page.keyboard.press('Enter')
                expect(page.locator('[data-testid="edit-name"]')).to_be_focused()
                capture(page, 'agents-edit')
                page.locator('[data-testid="edit-name"]').fill('Existing local renamed')
                page.locator('[data-edit-cap="ai_api"]').check()
                page.locator('[data-testid="edit-save"]').click()
                expect(page.locator('[data-testid="agent-detail"]')).to_have_count(0)
                expect(page.locator(f'[data-agent="{local_id}"] .agent-title')).to_contain_text('Existing local renamed')
                after = env.agent(local_id).json()
                for key in ['enabled','location','connection_mode','local_executable_path']:
                    assert after[key]==before[key]
                assert after['configured_capabilities']['storage'] is True
                assert after['name']=='Existing local renamed' and after['configured_capabilities']['ai_api'] is True
                evidence['hidden_values_preserved'] = True
                page.keyboard.press('Escape')
                page.reload()
                expect(page.locator(f'[data-agent="{local_id}"] .agent-title')).to_contain_text('Existing local renamed')
                capture(page, 'agents-refreshed')
                page.set_viewport_size({'width':390,'height':844})
                page.locator(f'[data-edit-agent="{local_id}"]').click()
                narrow=page.locator('.agent-settings-dialog').bounding_box()
                assert narrow and narrow['x']>=0 and narrow['x']+narrow['width']<=390
                assert page.evaluate('document.documentElement.scrollWidth')<=390
                evidence['narrow_modal_box']=narrow
                capture(page,'agents-narrow-edit')
                page.keyboard.press('Escape')
                page.locator(f'[data-delete-agent="{aid}"]').click()
                page.locator('[data-testid="agent-confirm-cancel"]').click()
                assert env.agent(aid).status_code==200
                page.locator(f'[data-delete-agent="{aid}"]').click()
                page.locator('[data-testid="agent-confirm-ok"]').click()
                expect(page.locator(f'.agent-row[data-agent="{aid}"]')).to_have_count(0)
                assert env.agent(aid).status_code==404
                page.reload();expect(page.locator('.agent-row')).to_have_count(2)
                assert env.agent(remote_id).json()==remote
                context.close()
                nonadmin=context_for(browser,'M')
                page=nonadmin.new_page()
                start=len(evidence['requests'])
                page.goto(url+'?agent='+local_id)
                expect(page.locator('[data-testid="settings-access-notice"]')).to_be_visible()
                expect(page.locator('[data-panel="system-agents"]')).to_have_count(0)
                expect(page.locator('[data-scope-card="me"]')).not_to_contain_text('에이전트')
                assert not any('/system/agents' in r['path'] for r in evidence['requests'][start:])
                capture(page,'agents-nonadmin-blocked')
                denied=env.client.get(API+'/system/agents',headers=env.h('M'))
                assert denied.status_code==403
                evidence['nonadmin_management_requests']=0
                evidence['nonadmin_server_status']=403
                evidence['completed']=True
                nonadmin.close()
            finally: browser.close()
    finally:
        (scratch/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill();process.wait(timeout=10)
        log.close()
