"""Opt-in Chromium integration: live group source + real migrated SQLite/API.

Set FG_BROWSER_ARTIFACTS to an isolated scratch directory and install Playwright's
Chromium there. Browser API transport is bridged to the existing TestClient;
authentication/provider fixtures are synthetic, group/profile persistence is real.
This is not evidence that flowgate.test has deployed this source.
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
from test_ai_group_role_profiles_0120 import world  # noqa: F401 - reuse real DB/API fixture


@pytest.mark.skipif(not os.environ.get('FG_BROWSER_ARTIFACTS'), reason='opt-in browser fixture requires scratch artifacts directory')
def test_role_entry_create_save_refresh_and_general_preservation(world):
    from playwright.sync_api import sync_playwright, expect
    scratch = Path(os.environ['FG_BROWSER_ARTIFACTS']).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[4]
    client = repo / 'client_gui'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # No source-tree caches or generated browser files.
    aliases = {'@': str(client/'src'), '@shared': str(repo/'client/shared')}
    aliases.update({name:str(client/'node_modules'/name) for name in ['vue','vue-i18n','axios']})
    config = scratch/'browser-vite.config.mjs'
    config.write_text('import vue from '+json.dumps((client/'node_modules/@vitejs/plugin-vue/dist/index.mjs').as_uri())+';\nexport default '+json.dumps({
        'root':str(client), 'base':'/app/', 'cacheDir':str(scratch/'vite-cache'),
        'resolve':{'preserveSymlinks':True,'alias':aliases},
        'define':{'import.meta.env.VITE_API_BASE_URL':json.dumps('/flowgate')},
        'server':{'host':'127.0.0.1','port':port,'strictPort':True,'fs':{'allow':[str(repo),str(scratch)]}},
    })[:-1]+', plugins: [vue()] };',encoding='utf-8')
    env = {**os.environ, 'TEMP':str(scratch), 'TMP':str(scratch)}
    log = (scratch/'vite.log').open('w',encoding='utf-8')
    process = subprocess.Popen([shutil.which('node'),str(client/'node_modules/vite/bin/vite.js'),'--config',str(config)],cwd=client,env=env,stdout=log,stderr=log,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    base=f'http://127.0.0.1:{port}/app/tests/browser/aiRoleEntry.0120.html'
    evidence={'url':base,'fixture':'real SQLite/API; TestClient transport; synthetic admin/providers','requests':[],'screens':[]}
    general=world['groups'].create_group({'name':'Existing general','description':'preserve me','group_type':'general'})
    gid=general['ai_group_id']
    member=world['groups'].add_member(gid,'p1')['members'][0]['member_id']
    custom=world['groups'].create_role(gid,{'name':'Existing custom role'})['roles'][0]['role_id']
    world['groups'].set_member_roles(gid,member,[custom])
    world['groups'].update_group(gid,{'name':'Existing general','supervisor_member_id':member},supervisor_given=True)
    before=world['groups'].get_group(gid)
    def bridge(route):
        request=route.request
        url=urlsplit(request.url)
        path=url.path.removeprefix('/flowgate')+('?' + url.query if url.query else '')
        result=world['client'].request(request.method,path,content=request.post_data,
            headers={'X-User':'admin','Content-Type':'application/json','X-Locale':'ko'})
        evidence['requests'].append({'method':request.method,'path':path,'status':result.status_code})
        route.fulfill(status=result.status_code,content_type='application/json',body=result.content)
    def capture(page,name):
        page.screenshot(path=str(scratch/(name+'.png')),full_page=True)
        evidence['screens'].append({'name':name,'url':page.url})
    try:
        for _ in range(100):
            assert process.poll() is None, 'Vite stopped; see scratch/vite.log'
            try:
                urllib.request.urlopen(base,timeout=1).close()
                break
            except (OSError, TimeoutError): time.sleep(.1)
        else: pytest.fail('Vite fixture startup timed out')
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.route('**/flowgate/api/v1/**',bridge)
                page.goto(base)
                expect(page.locator('.role-entry')).to_contain_text('AI 역할 그룹이 없습니다.')
                assert len(world['groups_repo'].list_all())==1
                capture(page,'isolated-empty')
                page.get_by_role('button',name='AI 역할 그룹 만들기',exact=True).click()
                expect(page.locator('#ai-group-edit-type')).to_have_value('role')
                page.locator('#ai-group-edit-name').fill('Browser roles')
                capture(page,'isolated-create-dialog')
                page.get_by_role('button',name='그룹 만들기',exact=True).click()
                expect(page.locator('.role-matrix-row')).to_have_count(1)
                expect(page.locator('.group-detail-head h2')).to_have_text('Browser roles')
                rid=world['groups_repo'].get_role_group()['ai_group_id']
                expect(page.locator('.add-kind-select option')).to_have_text([
                    '＋ 카드 유형 추가', '기본 설계', '프로토콜', '로직', 'DB설계', '조사', '작업계획', '작업', '테스트'])
                evidence['palette_types'] = page.locator('.add-kind-select option').all_text_contents()[1:]
                capture(page,'isolated-common')
                page.locator('.add-kind-select').select_option('T')
                expect(page.locator('[data-type="T"]')).to_be_visible()
                page.locator('[data-type="common"] [data-role="instruction"]').select_option('provider:p1')
                expect(page.locator('[data-type="T"] [data-role="instruction"] option').first).to_have_text('공용 · Model One')
                page.locator('[data-type="T"] [data-role="review"]').select_option('not_used')
                page.locator('[data-type="T"] [data-role="execution"]').select_option('provider:p2')
                page.locator('[data-type="common"] [data-role="instruction"]').select_option('provider:p2')
                expect(page.locator('[data-type="T"] [data-role="instruction"] option').first).to_have_text('공용 · Model Two')
                expect(page.locator('[data-type="T"] [data-role="review"]')).to_have_value('not_used')
                expected=world['profiles'].profiles(rid)
                evidence['saved_profiles']=expected
                capture(page,'isolated-saved')
                page.reload()
                expect(page.locator('[data-type="T"] [data-role="review"]')).to_have_value('not_used')
                expect(page.locator('[data-type="T"] [data-role="execution"]')).to_have_value('provider:p2')
                expect(page.locator('[data-type="T"] [data-role="instruction"]')).to_have_value('inherit')
                expect(page.locator('[data-type="T"] [data-role="instruction"] option').first).to_have_text('공용 · Model Two')
                assert world['profiles'].profiles(rid)==expected
                evidence['refreshed_profiles']=world['client'].get(f'/api/v1/ai-groups/{rid}/role-profiles',headers={'X-User':'admin'}).json()
                assert evidence['refreshed_profiles']==expected
                capture(page,'isolated-refreshed')
                page.locator('.group-index-item').filter(has_text='Existing general').click()
                expect(page.locator('.role-matrix')).to_have_count(0)
                expect(page.locator('.member-row')).to_have_count(1)
                assert world['groups'].get_group(gid)==before
                capture(page,'isolated-general')
                page.set_viewport_size({'width':390,'height':844})
                page.locator('.group-index-item').filter(has_text='Browser roles').click()
                scroller = page.locator('.role-grid-wrap')
                scroller.evaluate('(el) => { el.scrollLeft = el.scrollWidth }')
                review_box = page.locator('[data-type="common"] [data-role="review"]').bounding_box()
                assert review_box and review_box['x'] >= 0 and review_box['x'] + review_box['width'] <= 390
                assert page.evaluate('document.documentElement.scrollWidth') <= 390
                page.locator('[data-type="common"] [data-role="review"]').select_option('not_used')
                expect(page.locator('[data-type="common"] [data-role="review"]')).to_have_value('not_used')
                capture(page,'isolated-narrow')
                evidence['narrow_review_visible'] = review_box
                scroller.evaluate('(el) => { el.scrollLeft = 0 }')
                page.locator('[data-type="T"] .remove-kind').click()
                expect(page.locator('[data-type="T"]')).to_have_count(0)
                page.reload()
                expect(page.locator('.role-matrix-row')).to_have_count(1)
                assert len(world['profiles'].profiles(rid)['items'])==1
                assert world['groups'].get_group(gid)==before
                evidence['final_profiles']=world['profiles'].profiles(rid)
                evidence['general_preserved']=True
                evidence['completed']=True
            finally: browser.close()
    finally:
        (scratch/'browser-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
        log.close()
