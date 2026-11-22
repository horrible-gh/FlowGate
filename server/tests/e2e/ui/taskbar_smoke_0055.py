"""Opt-in real-browser smoke for the isolated FlowGate test server.

Run: python tests/e2e/ui/taskbar_smoke_0055.py --base-url http://flowgate.test
Never targets staging/production. Uses only the documented test account.
"""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    args = parser.parse_args()
    assert urlparse(args.base_url).hostname in ('flowgate.test', 'flowgate.test2')
    base = args.base_url.rstrip('/')
    with sync_playwright() as p:
        # Chat commands isolate LOCALAPPDATA; system-installed browsers are independent of that cache.
        candidates = [Path(root) / suffix for root in (os.environ.get('ProgramW6432'), os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)')) if root for suffix in ('Google/Chrome/Application/chrome.exe', 'Microsoft/Edge/Application/msedge.exe')]
        if os.name == 'nt':
            import winreg
            # Discover the installed browser cache from Windows, rather than the command's temporary profile.
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders') as key:
                local_app_data = Path(winreg.QueryValueEx(key, 'Local AppData')[0])
            candidates.extend((local_app_data / 'ms-playwright').glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
            candidates.extend((local_app_data / 'ms-playwright').glob('chromium-*/chrome-win64/chrome.exe'))
        installed = next((str(path) for path in candidates if path.is_file()), None)
        print('BROWSER', installed or 'playwright chromium', flush=True)
        browser = p.chromium.launch(headless=True, executable_path=installed)
        context = browser.new_context(viewport={'width': 1568, 'height': 875})
        response = context.request.post(base + '/flowgate/auth/login', data={'username': 'test', 'password': 'test1234', 'remember_me': False})
        assert response.ok, f'login HTTP {response.status}'
        token = response.json()['access_token']
        context.add_init_script('sessionStorage.setItem("fg_access_token", '+json.dumps(token)+'); localStorage.setItem("preferred_locale", "ko");')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
        page.goto(base + '/app/designer')
        page.locator('[data-test="designer-taskbar"]').wait_for()
        page.locator('[data-test="designer-body"]').wait_for(state='visible')
        page.locator('[data-test="designer-menu"]').filter(has_text='파일').click()
        page.locator('[data-command="project.open"]').click()
        page.locator('.project-row').first.wait_for()
        project = page.locator('.project-row').filter(has=page.locator('.project-row-name', has_text='FlowGate')).first
        if not project.count(): project = page.locator('.project-row').first
        project.locator('.project-row-open').click()
        page.locator('[data-test="project-explorer-tree"]').wait_for()
        page.locator('[data-test="designer-menu"]').filter(has_text='파일').click()
        page.locator('[data-command="flow.open"]').click()
        page.locator('[data-test="designer-open-item"]').first.wait_for()
        page.locator('[data-test="designer-open-item"]').first.click()
        page.locator('.vue-flow__node').first.wait_for()
        documents = page.locator('[data-test="designer-canvas-node-document"]')
        documents.first.click()
        panel = page.locator('.ui-modeless-panel').first
        panel.wait_for(state='visible')
        title = panel.locator('.ui-modeless-titlebar').bounding_box()
        page.mouse.move(title['x']+100, title['y']+12)
        page.mouse.down(); page.mouse.move(title['x']+140, title['y']+32); page.mouse.up()
        handle = panel.locator('.ui-modeless-resize-handle').bounding_box()
        page.mouse.move(handle['x']+3, handle['y']+3)
        page.mouse.down(); page.mouse.move(handle['x']+33, handle['y']+23); page.mouse.up()
        geometry = panel.bounding_box()
        panel.locator('[data-modeless-action="minimize"]').click()
        assert not panel.is_visible()
        page.locator('.minimized-item').first.click()
        assert panel.bounding_box() == geometry, (geometry, panel.bounding_box())
        panel.locator('[data-modeless-action="minimize"]').click()
        documents.first.click()
        assert page.locator('.ui-modeless-panel').count() == 1
        assert panel.is_visible() and panel.bounding_box() == geometry
        panel.locator('[data-modeless-action="minimize"]').click()
        for index in range(1, documents.count()):
            documents.nth(index).click()
            page.locator('.ui-modeless-panel:visible [data-modeless-action="minimize"]').click()
        page.locator('[data-test="designer-menu"]').filter(has_text='파일').click()
        page.locator('[data-command="flow.open"]').click()
        page.locator('[data-test="designer-open-item"]').nth(1).click()
        page.locator('.vue-flow__node').first.wait_for()
        documents.first.click()
        page.locator('.ui-modeless-panel:visible [data-modeless-action="minimize"]').click()
        assert page.locator('.ui-modeless-panel').count() >= 4
        page.set_viewport_size({'width':1100, 'height':760})
        page.locator('.overflow-trigger').wait_for()
        page.locator('.overflow-trigger').click()
        page.locator('.overflow-list [role="menuitem"]').first.click()
        assert page.locator('.ui-modeless-panel:visible').count() == 1
        page.locator('.ui-modeless-panel:visible [data-modeless-action="close"]').click()
        print('WINDOWS_OK drag resize minimize restore duplicate-open overflow close')
        for width, height in [(1568, 875), (1100, 760)]:
            page.set_viewport_size({'width': width, 'height': height})
            bar = page.locator('[data-test="designer-taskbar"]')
            box = bar.bounding_box()
            assert box and box['width'] == width and box['height'] == 50, box
            assert abs(box['y'] + box['height'] - height) <= 1, box
            assert page.locator('[data-test="designer-header-settings"]').count() == 0
            bar.locator('.menu-trigger').click()
            assert [c.get_attribute('data-card') for c in bar.locator('[role="menuitem"]').all()] == ['my-ai', 'question-policy', 'theme', 'agents', 'account']
            # The board is two cards wide: the two AI cards sit side by side on one row.
            ai_cards = [bar.locator(f'[data-card="{key}"]').bounding_box() for key in ('my-ai', 'question-policy')]
            assert abs(ai_cards[0]['y'] - ai_cards[1]['y']) <= 1 and ai_cards[1]['x'] >= ai_cards[0]['x'] + ai_cards[0]['width'], ai_cards
            assert bar.locator('.menu-group-label').all_inner_texts() == ['내 AI 관리', '내 설정']
            # Every menu card keeps the canvas-card size and must not stretch across the board.
            board = bar.locator('.menu-board').bounding_box()
            assert board['y'] >= 0 and board['y'] + board['height'] <= box['y'], (board, box)
            for menu_card in bar.locator('.menu-card').all():
                card = menu_card.bounding_box()
                assert card and 160 <= card['width'] <= 176 and abs(card['height'] - 92) <= 1, card
                print('CARD_OK', width, height, card)
            page.keyboard.press('Escape')
            assert bar.locator('[role="menu"]').count() == 0
            bar.locator('.menu-trigger').click()
            page.locator('[data-test="designer-header-brand"]').click()
            assert bar.locator('[role="menu"]').count() == 0
            dock = page.locator('[data-test="designer-bottom-dock"]').bounding_box()
            assert dock and dock['y']+dock['height'] <= box['y']+1, (dock, box)
            print('LAYOUT_OK', width, height, box)
        headers = {'Authorization': 'Bearer '+token}
        before = context.request.get(base+'/flowgate/api/v1/me/ui-settings', headers=headers).json()['settings']
        dock_handle = page.locator('[data-test="designer-bottom-resize"]').bounding_box()
        page.mouse.move(dock_handle['x']+dock_handle['width']/2, dock_handle['y']+2)
        page.mouse.down(); page.mouse.move(dock_handle['x']+dock_handle['width']/2, dock_handle['y']-28); page.mouse.up()
        page.wait_for_function("fetch('/flowgate/api/v1/me/ui-settings', {headers:{Authorization:'Bearer '+sessionStorage.getItem('fg_access_token')}}).then(r=>r.json()).then(r=>r.settings.designer_bottom_height !== " + str(before['designer_bottom_height']) + ")")
        resized = context.request.get(base+'/flowgate/api/v1/me/ui-settings', headers=headers).json()['settings']
        page.reload()
        page.locator('[data-test="designer-taskbar"]').wait_for()
        page.wait_for_function("Math.abs(document.querySelector('[data-test=designer-bottom-dock]').getBoundingClientRect().height - " + str(resized['designer_bottom_height']) + ") < 2")
        restored = context.request.patch(base+'/flowgate/api/v1/me/ui-settings', headers=headers, data={'designer_bottom_height':before['designer_bottom_height']})
        assert restored.ok
        print('DOCK_OK drag save reload restore')
        # The theme card opens a window over the Designer; the page stays where it is.
        evidence = os.environ.get('TASKBAR_SMOKE_SHOTS')
        for width, height in [(1568, 875), (1100, 760)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.locator('.menu-trigger').click()
            card = page.locator('[data-card="theme"]')
            assert card.get_attribute('data-card-state') == 'closed'
            assert card.locator('[data-test="menu-card-dot"]').count() == 0
            card_text = card.inner_text()
            if evidence: page.screenshot(path=str(Path(evidence) / f'theme_menu_{width}x{height}.png'))
            card.click()
            window = page.locator('.ui-modeless-panel:visible').filter(has=page.locator('[data-test="me-theme-window"]'))
            window.wait_for()
            assert '/designer' in page.url, page.url
            window.locator('#taskbar-style-window').wait_for()
            box = window.bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width and box['y'] + box['height'] <= height - 50, (box, width, height)
            assert window.locator('#taskbar-style-window option').all_text_contents() == ['FlowGate']
            if evidence: page.screenshot(path=str(Path(evidence) / f'theme_window_{width}x{height}.png'))
            window.locator('[data-modeless-action="minimize"]').click()
            assert not window.is_visible()
            item = page.locator('.minimized-item').filter(has_text='테마')
            assert item.count() == 1
            # The taskbar item shows the window title only; the card shows a dot, no status wording.
            assert item.locator('.item-text').inner_text().strip() == '내 설정 · 테마', item.inner_text()
            page.locator('.menu-trigger').click()
            assert card.get_attribute('data-card-state') == 'minimized'
            assert card.locator('[data-test="menu-card-dot"]').is_visible()
            assert card.inner_text() == card_text, (card_text, card.inner_text())
            assert page.get_by_text('접힘').count() == 0
            if evidence: page.screenshot(path=str(Path(evidence) / f'theme_minimized_{width}x{height}.png'))
            card.click()
            window.wait_for()
            assert page.locator('[data-test="me-theme-window"]').count() == 1
            assert window.bounding_box() == box, (box, window.bounding_box())
            if width == 1100:
                window.get_by_role('button', name='저장', exact=True).click()
                window.get_by_role('status').filter(has_text='저장').wait_for()
            window.locator('[data-modeless-action="close"]').click()
            assert page.locator('[data-test="me-theme-window"]').count() == 0
            print('THEME_WINDOW_OK', width, height, box)
        # The my AI card opens the AI list as a window over the Designer.
        for width, height in [(1568, 875), (1100, 760)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.locator('.menu-trigger').click()
            card = page.locator('[data-card="my-ai"]')
            assert card.get_attribute('data-card-state') == 'closed'
            if evidence: page.screenshot(path=str(Path(evidence) / f'myai_menu_{width}x{height}.png'))
            card.click()
            window = page.locator('.ui-modeless-panel:visible').filter(has=page.locator('[data-test="my-ai-window"]'))
            window.wait_for()
            assert '/designer' in page.url, page.url
            window.locator('.detail-toolbar').wait_for()
            box = window.bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width and box['y'] >= 0 and box['y'] + box['height'] <= height - 50, (box, width, height)
            # Nothing in the list may be cut off sideways inside the window.
            body = window.locator('[data-test="my-ai-window"]')
            assert body.evaluate('el => [...el.querySelectorAll(".detail-head, .detail-toolbar, .provider-card")].every(n => n.scrollWidth <= n.clientWidth + 1 && n.getBoundingClientRect().right <= el.getBoundingClientRect().right + 1)')
            assert window.locator('.page-title').is_hidden()
            # My own AI only: no owner filter and no system AI card, for an administrator too.
            assert window.locator('.segmented').count() == 0
            assert window.locator('.provider-card .mode-icon.owner.system').count() == 0
            print('MYAI_MINE_ONLY cards', window.locator('.provider-card').count())
            if evidence: page.screenshot(path=str(Path(evidence) / f'myai_window_{width}x{height}.png'))
            # The add dialog opens above the window and closes again.
            window.get_by_role('button', name='AI 추가').click()
            dialog = page.get_by_role('dialog').last
            dialog.wait_for()
            if evidence and width == 1100: page.screenshot(path=str(Path(evidence) / f'myai_add_{width}x{height}.png'))
            page.keyboard.press('Escape')
            if dialog.is_visible(): dialog.get_by_role('button', name='취소').click()
            dialog.wait_for(state='hidden')
            window.locator('[data-modeless-action="minimize"]').click()
            assert not window.is_visible()
            item = page.locator('.minimized-item').filter(has_text='내 AI')
            assert item.locator('.item-text').inner_text().strip() == 'AI 관리 · 내 AI', item.inner_text()
            page.locator('.menu-trigger').click()
            assert card.get_attribute('data-card-state') == 'minimized'
            assert card.locator('[data-test="menu-card-dot"]').is_visible()
            if evidence: page.screenshot(path=str(Path(evidence) / f'myai_minimized_{width}x{height}.png'))
            card.click()
            window.wait_for()
            assert page.locator('[data-test="my-ai-window"]').count() == 1
            assert window.bounding_box() == box, (box, window.bounding_box())
            window.locator('[data-modeless-action="close"]').click()
            assert page.locator('[data-test="my-ai-window"]').count() == 0
            print('MYAI_WINDOW_OK', width, height, box)
        # The question policy card opens the policy box as a window over the Designer. Nothing is saved here.
        for width, height in [(1568, 875), (1100, 760)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.locator('.menu-trigger').click()
            card = page.locator('[data-card="question-policy"]')
            assert card.get_attribute('data-card-state') == 'closed'
            if evidence: page.screenshot(path=str(Path(evidence) / f'policy_menu_{width}x{height}.png'))
            card.click()
            window = page.locator('.ui-modeless-panel:visible').filter(has=page.locator('[data-test="question-policy-window"]'))
            window.wait_for()
            assert '/designer' in page.url, page.url
            text = window.locator('#question-policy-body')
            text.wait_for()
            page.wait_for_function("!document.querySelector('#question-policy-body').disabled")
            box = window.bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width and box['y'] >= 0 and box['y'] + box['height'] <= height - 50, (box, width, height)
            # The text box, [AI와 정하기] and [저장] all fit inside the window with no scrolling.
            body = window.locator('.ui-modeless-body')
            assert body.evaluate('el => el.scrollHeight <= el.clientHeight + 1 && el.scrollWidth <= el.clientWidth + 1'), body.evaluate('el => [el.scrollHeight, el.clientHeight, el.scrollWidth, el.clientWidth]')
            assert window.get_by_role('button', name='AI와 정하기').is_visible()
            assert window.get_by_role('button', name='저장', exact=True).is_visible()
            assert text.bounding_box()['height'] >= 96
            if evidence: page.screenshot(path=str(Path(evidence) / f'policy_window_{width}x{height}.png'))
            saved_text = text.input_value()
            text.fill(saved_text + ' (0055 점검)')
            window.locator('[data-modeless-action="minimize"]').click()
            assert not window.is_visible()
            item = page.locator('.minimized-item').filter(has_text='질의 방침')
            assert item.locator('.item-text').inner_text().strip() == 'AI 관리 · 질의 방침', item.inner_text()
            page.locator('.menu-trigger').click()
            assert card.get_attribute('data-card-state') == 'minimized'
            assert card.locator('[data-test="menu-card-dot"]').is_visible()
            if evidence: page.screenshot(path=str(Path(evidence) / f'policy_minimized_{width}x{height}.png'))
            card.click()
            window.wait_for()
            assert page.locator('[data-test="question-policy-window"]').count() == 1
            assert window.bounding_box() == box, (box, window.bounding_box())
            # Text typed before minimizing is still there, and is thrown away by closing without [저장].
            assert text.input_value() == saved_text + ' (0055 점검)'
            window.locator('[data-modeless-action="close"]').click()
            assert page.locator('[data-test="question-policy-window"]').count() == 0
            policy = context.request.get(base+'/flowgate/api/v1/me/question-policy', headers=headers).json()
            assert policy['body'] == saved_text, policy
            print('POLICY_WINDOW_OK', width, height, box)
        # The agents card opens my own Agent list as a window over the Designer. Nothing is registered here.
        agents_before = context.request.get(base+'/flowgate/api/v1/me/agents', headers=headers).json()['agents']
        for width, height in [(1568, 875), (1100, 760)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.locator('.menu-trigger').click()
            card = page.locator('[data-card="agents"]')
            assert card.get_attribute('data-card-state') == 'closed'
            # The theme and agents cards sit side by side on the settings row.
            row = [page.locator(f'[data-card="{key}"]').bounding_box() for key in ('theme', 'agents')]
            assert abs(row[0]['y'] - row[1]['y']) <= 1 and row[1]['x'] >= row[0]['x'] + row[0]['width'], row
            if evidence: page.screenshot(path=str(Path(evidence) / f'agents_menu_{width}x{height}.png'))
            card.click()
            window = page.locator('.ui-modeless-panel:visible').filter(has=page.locator('[data-test="me-agents-window"]'))
            window.wait_for()
            assert '/designer' in page.url, page.url
            window.locator('[data-testid="agent-list"]').wait_for()
            box = window.bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width and box['y'] >= 0 and box['y'] + box['height'] <= height - 50, (box, width, height)
            # The settings page frame stays out of the window, and nothing is cut off sideways.
            body = window.locator('[data-test="me-agents-window"]')
            assert body.locator('.fgs-layout, .settings-shell-view').count() == 0
            assert body.locator('.agent-page-head h2').is_hidden()
            assert window.locator('.ui-modeless-body').evaluate('el => el.scrollWidth <= el.clientWidth + 1')
            rows = body.locator('[data-testid="agent-list"] .agent-row')
            assert rows.count() == len(agents_before), (rows.count(), len(agents_before))
            if not agents_before: assert body.locator('[data-testid="agents-empty"]').is_visible()
            print('AGENTS_LIST rows', rows.count())
            if evidence: page.screenshot(path=str(Path(evidence) / f'agents_window_{width}x{height}.png'))
            # The registration dialog opens above the window and closes again without registering.
            window.locator('[data-testid="agent-new"]').click()
            form = page.locator('[data-testid="agent-create-form"]')
            form.wait_for()
            if evidence and width == 1100: page.screenshot(path=str(Path(evidence) / f'agents_register_{width}x{height}.png'))
            page.locator('[data-testid="agent-editor-close"]').click()
            form.wait_for(state='hidden')
            window.locator('[data-modeless-action="minimize"]').click()
            assert not window.is_visible()
            item = page.locator('.minimized-item').filter(has_text='에이전트')
            assert item.locator('.item-text').inner_text().strip() == '내 설정 · 에이전트', item.inner_text()
            page.locator('.menu-trigger').click()
            assert card.get_attribute('data-card-state') == 'minimized'
            assert card.locator('[data-test="menu-card-dot"]').is_visible()
            if evidence: page.screenshot(path=str(Path(evidence) / f'agents_minimized_{width}x{height}.png'))
            card.click()
            window.wait_for()
            assert page.locator('[data-test="me-agents-window"]').count() == 1
            assert window.bounding_box() == box, (box, window.bounding_box())
            window.locator('[data-modeless-action="close"]').click()
            assert page.locator('[data-test="me-agents-window"]').count() == 0
            print('AGENTS_WINDOW_OK', width, height, box)
        agents_after = context.request.get(base+'/flowgate/api/v1/me/agents', headers=headers).json()['agents']
        assert [a['agent_id'] for a in agents_after] == [a['agent_id'] for a in agents_before]
        # The account card opens my sign-in information and the display language as a window over the Designer.
        for width, height in [(1568, 875), (1100, 760)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.locator('.menu-trigger').click()
            card = page.locator('[data-card="account"]')
            assert card.get_attribute('data-card-state') == 'closed'
            # Three settings cards on a board two cards wide: the account card starts the next line, under the theme card.
            row = [page.locator(f'[data-card="{key}"]').bounding_box() for key in ('theme', 'agents', 'account')]
            assert row[2]['y'] >= row[0]['y'] + row[0]['height'] and abs(row[2]['x'] - row[0]['x']) <= 1, row
            assert all(abs(b['width'] - 174) <= 1 and abs(b['height'] - 92) <= 1 for b in row), row
            board = page.locator('.menu-board').bounding_box()
            assert board['y'] >= 0 and board['y'] + board['height'] <= height - 50, (board, height)
            if evidence: page.screenshot(path=str(Path(evidence) / f'account_menu_{width}x{height}.png'))
            card.click()
            window = page.locator('.ui-modeless-panel:visible').filter(has=page.locator('[data-test="me-account-window"]'))
            window.wait_for()
            assert '/designer' in page.url, page.url
            box = window.bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width and box['y'] >= 0 and box['y'] + box['height'] <= height - 50, (box, width, height)
            body = window.locator('[data-test="me-account-window"]')
            assert body.locator('.fgs-layout, .settings-shell-view').count() == 0
            assert body.locator('.fgs-phead').is_hidden()
            assert body.locator('[data-testid="me-username"]').inner_text().strip() == 'test'
            assert body.locator('[data-card="retention"], #me-retention').count() == 0
            assert body.locator('[data-locale]').count() == 3
            # Nothing is cut off sideways and the two sections fit without scrolling.
            assert window.locator('.ui-modeless-body').evaluate('el => el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1')
            if evidence: page.screenshot(path=str(Path(evidence) / f'account_window_{width}x{height}.png'))
            if width == 1100:
                # The language buttons work from the window: the window title follows, then back to Korean.
                body.locator('[data-locale="en"]').click()
                page.wait_for_function("document.querySelector('.ui-modeless-panel [data-test=\"me-account-window\"]').closest('.ui-modeless-panel').querySelector('.ui-modeless-title').textContent.trim() === 'My settings · Account'")
                if evidence: page.screenshot(path=str(Path(evidence) / f'account_window_en_{width}x{height}.png'))
                body.locator('[data-locale="ja"]').click()
                page.wait_for_function("document.querySelector('.ui-modeless-panel [data-test=\"me-account-window\"]').closest('.ui-modeless-panel').querySelector('.ui-modeless-title').textContent.trim().includes('アカウント')")
                body.locator('[data-locale="ko"]').click()
                page.wait_for_function("document.querySelector('.ui-modeless-panel [data-test=\"me-account-window\"]').closest('.ui-modeless-panel').querySelector('.ui-modeless-title').textContent.trim() === '내 설정 · 계정·화면'")
            window.locator('[data-modeless-action="minimize"]').click()
            assert not window.is_visible()
            item = page.locator('.minimized-item').filter(has_text='계정·화면')
            assert item.locator('.item-text').inner_text().strip() == '내 설정 · 계정·화면', item.inner_text()
            page.locator('.menu-trigger').click()
            assert card.get_attribute('data-card-state') == 'minimized'
            assert card.locator('[data-test="menu-card-dot"]').is_visible()
            if evidence: page.screenshot(path=str(Path(evidence) / f'account_minimized_{width}x{height}.png'))
            card.click()
            window.wait_for()
            assert page.locator('[data-test="me-account-window"]').count() == 1
            assert window.bounding_box() == box, (box, window.bounding_box())
            window.locator('[data-modeless-action="close"]').click()
            assert page.locator('[data-test="me-account-window"]').count() == 0
            print('ACCOUNT_WINDOW_OK', width, height, box)
        # The bell's AI tab opens short: at most 6 rows, the rest behind "show more".
        page.locator('[data-test="designer-header-notifications"]').click()
        page.locator('#notification-tab-ai').click()
        page.locator('#notification-panel-ai').wait_for()
        page.wait_for_function("!document.querySelector('#designer-notifications [role=\"status\"]')")
        rows = page.locator('[data-test="notification-ai"]')
        assert rows.count() <= 6, rows.count()
        more = page.locator('[data-test="notification-ai-more"]')
        print('AI_TAB rows', rows.count(), 'more', more.count(), 'height', page.locator('#designer-notifications').bounding_box()['height'])
        if evidence: page.screenshot(path=str(Path(evidence) / 'bell_ai_short_1100x760.png'))
        if more.count():
            assert page.locator('#designer-notifications').bounding_box()['height'] <= 600
            more.click()
            assert rows.count() > 6
            if evidence: page.screenshot(path=str(Path(evidence) / 'bell_ai_more_1100x760.png'))
            more.click()
            assert rows.count() == 6
        page.keyboard.press('Escape')
        # The settings page's account screen no longer has the finished-card retention row.
        page.goto(base + '/app/settings/me/account')
        page.locator('[data-panel="me-account"]').wait_for()
        assert page.locator('[data-card="retention"], #me-retention').count() == 0
        assert page.locator('[data-panel="me-account"] [data-locale]').count() == 3
        if evidence: page.screenshot(path=str(Path(evidence) / 'account_page_1100x760.png'))
        after = context.request.get(base+'/flowgate/api/v1/me/ui-settings', headers=headers).json()['settings']
        assert after['designer_taskbar_style'] == 'flowgate'
        for key in ('designer_left_width','designer_right_width','designer_bottom_height','ai_finished_card_retention_minutes'):
            assert before[key] == after[key], (key, before, after)
        # The settings page shows the same panel.
        page.goto(base + '/app/settings/me/theme')
        page.locator('#taskbar-style').wait_for()
        assert page.locator('#taskbar-style option').all_text_contents() == ['FlowGate']
        assert page.locator('#taskbar-style').input_value() == 'flowgate'
        second = browser.new_context(viewport={'width':1568,'height':875})
        second_login = second.request.post(base+'/flowgate/auth/login', data={'username':'test','password':'test1234','remember_me':False})
        assert second_login.ok
        second_token = second_login.json()['access_token']
        second.add_init_script('sessionStorage.setItem("fg_access_token", '+json.dumps(second_token)+');')
        second_page = second.new_page()
        second_page.goto(base+'/app/settings/me/theme')
        second_page.locator('#taskbar-style').wait_for()
        assert second_page.locator('#taskbar-style').input_value() == 'flowgate'
        second.close()
        print('SETTINGS_OK reload independent-session preservation', {k:after[k] for k in after if k!='updated_at'})
        assert not errors, errors
        print('CONSOLE_OK')
        context.close(); browser.close()

if __name__ == '__main__': main()
