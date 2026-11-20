"""Manual restart probe for the documented disposable FlowGate test server.

Start this command, then restart FlowGate-Dev through SpawnPoint while it waits.
It observes the real application stream and never reloads the page after login.
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project', default='qa0048settings')
    parser.add_argument('--group', help='Existing Flow group to open for panel recovery checks')
    parser.add_argument('--base-url', default='http://flowgate.test', choices=['http://flowgate.test', 'http://flowgate.test2'])
    parser.add_argument('--executable-path', default=os.getenv('FLOWGATE_TEST_BROWSER'))
    parser.add_argument('--wait-seconds',type=int,default=180)
    args=parser.parse_args()
    with sync_playwright() as pw:
        # Command sandboxes redirect LOCALAPPDATA; use an existing system browser.
        candidates = [Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
                      Path('C:/Program Files/Google/Chrome/Application/chrome.exe')]
        candidates = list(Path('C:/Users').glob('*/AppData/Local/ms-playwright/chromium-*/chrome-win*/chrome.exe')) + candidates
        executable = next((p for p in candidates if p.is_file()), None)
        executable = args.executable_path or executable
        browser=pw.chromium.launch(headless=True, **({'executable_path': str(executable)} if executable else {}))
        page=browser.new_page()
        page.add_init_script("""
          window.__probe = {opens:0,errors:0,reads:0,firstError:null,lastOpen:null};
          const Native = window.EventSource;
          window.EventSource = class extends Native {
            constructor(...args) { super(...args);
              this.addEventListener('open',()=>{window.__probe.opens++;window.__probe.lastOpen=Date.now()});
              this.addEventListener('error',()=>{window.__probe.errors++;window.__probe.firstError??=Date.now()});
            }
          };
        """)
        page.goto(args.base_url+'/login?next=/app/designer',wait_until='domcontentloaded')
        page.locator('#fg-login-username').fill('test')
        page.locator('#fg-login-password').fill('test1234')
        page.locator('[data-testid="login-submit"]').click()
        page.wait_for_selector('[data-test="designer-shell"]',timeout=60000)
        page.locator('[data-test="designer-menu"][data-menu="file"]').click()
        page.locator('[data-command="project.open"]').click()
        page.locator(f'[data-project-id="{args.project}"] .project-row-open').click(timeout=30000)
        if args.group:
            page.locator('[data-test="project-explorer-include-archived"]').check()
            page.wait_for_timeout(1500)
            module = args.group.split('.')[1]
            for row_id in [f'project:{args.project}', f'module:{args.project}:{module}']:
                row = page.locator(f'[data-row-id="{row_id}"]')
                row.wait_for(timeout=30000)
                if row.get_attribute('aria-expanded') != 'true':
                    row.locator(':scope > .explorer-row > [data-test="project-explorer-caret"]').click()
            flow = page.locator(f'[data-test="project-explorer-flow"][data-row-id="{args.group}"]')
            flow.locator('.explorer-row').dblclick(timeout=30000)
            page.wait_for_timeout(3000)
        page.wait_for_function('window.__probe.opens > 0',timeout=60000)
        page.evaluate('window.__probe.errors=0;window.__probe.firstError=null')
        base=page.evaluate('window.__probe.opens')
        page.evaluate('window.__probe.documentIdentity = String(Date.now()) + String(Math.random())')
        identity=page.evaluate('window.__probe.documentIdentity')
        reads=[]
        recovered={}
        tracking=False
        def response_seen(response):
            if '/api/' not in response.url: return
            reads.append(response.status)
            if response.status != 200 or not tracking: return
            panel = ('tree' if '/tree' in response.url else
                     'chats' if '/documents?' in response.url and 'type_code=CH' in response.url else
                     'canvas' if '/canvas' in response.url else None)
            if panel: recovered.setdefault(panel, int(time.time()*1000))
        page.on('response', response_seen)
        print('READY: signed in, designer mounted, application SSE open',flush=True)
        page.wait_for_function('window.__probe.errors > 0',timeout=args.wait_seconds*1000)
        tracking=True
        if args.group:
            # First mount these panels during the outage so their failed reads must recover.
            page.locator('[data-test="designer-explorer-mode-tabs"] [role="tab"]').nth(1).click()
            page.locator('[data-test="designer-right-tool-tabs"] [role="tab"]').nth(0).click()
            page.locator('[data-test="conv-list-btn"]').click()
        page.wait_for_function('(base)=>window.__probe.opens > base',arg=base,timeout=args.wait_seconds*1000)
        page.wait_for_timeout(10000)
        assert page.evaluate('window.__probe.documentIdentity')==identity
        assert page.locator('[data-test="designer-shell"]').is_visible()
        assert page.evaluate('Boolean(sessionStorage.getItem("fg_access_token"))')
        assert 200 in reads, 'reconnected stream must trigger successful panel reads'
        if args.group:
            assert all(panel in recovered for panel in ('tree', 'chats', 'canvas')), recovered
            assert page.locator('[data-test="designer-file-explorer"] [role="tree"]').is_visible()
            assert page.locator('[data-test="dfx-load-error"]').count() == 0
            assert page.locator('[data-test="conv-list-pop"] .conv-list-state--error').count() == 0
            assert page.locator('[data-test="designer-bottom-dock"]').is_visible()
        probe=page.evaluate('window.__probe')
        print('PASS: same page recovered SSE and panel reads after server restart; '+json.dumps({
            **probe, 'observed_at': datetime.now(timezone.utc).isoformat(),
            'sse_recovery_ms': probe['lastOpen']-probe['firstError'],
            'panel_reads_after_disconnect_ms': {key: value-probe['firstError'] for key,value in recovered.items()},
        }))
        browser.close()
if __name__=='__main__':main()
