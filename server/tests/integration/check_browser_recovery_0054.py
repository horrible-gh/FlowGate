"""Manual restart probe for the documented disposable FlowGate test server.

Start this command, then restart FlowGate-Dev through SpawnPoint while it waits.
It observes the real application stream and never reloads the page after login.
"""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project', default='qa0048settings')
    parser.add_argument('--wait-seconds',type=int,default=180)
    args=parser.parse_args()
    with sync_playwright() as pw:
        # Command sandboxes redirect LOCALAPPDATA; use an existing system browser.
        candidates = [Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
                      Path('C:/Program Files/Google/Chrome/Application/chrome.exe')]
        candidates = list(Path('C:/Users').glob('*/AppData/Local/ms-playwright/chromium-*/chrome-win*/chrome.exe')) + candidates
        executable = next((p for p in candidates if p.is_file()), None)
        assert executable, 'No installed browser executable found'
        browser=pw.chromium.launch(headless=True, executable_path=str(executable))
        page=browser.new_page()
        page.add_init_script("""
          window.__probe = {opens:0,errors:0,reads:0};
          const Native = window.EventSource;
          window.EventSource = class extends Native {
            constructor(...args) { super(...args);
              this.addEventListener('open',()=>window.__probe.opens++);
              this.addEventListener('error',()=>window.__probe.errors++);
            }
          };
        """)
        page.goto('http://flowgate.test/login?next=/app/designer',wait_until='domcontentloaded')
        page.locator('#fg-login-username').fill('test')
        page.locator('#fg-login-password').fill('test1234')
        page.locator('[data-testid="login-submit"]').click()
        page.wait_for_selector('[data-test="designer-shell"]',timeout=60000)
        page.locator('[data-test="designer-menu"][data-menu="file"]').click()
        page.locator('[data-command="project.open"]').click()
        page.locator(f'[data-project-id="{args.project}"] .project-row-open').click(timeout=30000)
        page.wait_for_function('window.__probe.opens > 0',timeout=60000)
        base=page.evaluate('window.__probe.opens')
        page.evaluate('window.__probe.documentIdentity = String(Date.now()) + String(Math.random())')
        identity=page.evaluate('window.__probe.documentIdentity')
        reads=[]
        page.on('response',lambda response: reads.append(response.status) if '/api/' in response.url else None)
        print('READY: signed in, designer mounted, application SSE open',flush=True)
        page.wait_for_function('window.__probe.errors > 0',timeout=args.wait_seconds*1000)
        page.wait_for_function('(base)=>window.__probe.opens > base',arg=base,timeout=args.wait_seconds*1000)
        page.wait_for_timeout(5000)
        assert page.evaluate('window.__probe.documentIdentity')==identity
        assert page.locator('[data-test="designer-shell"]').is_visible()
        assert page.evaluate('Boolean(sessionStorage.getItem("fg_access_token"))')
        assert 200 in reads, 'reconnected stream must trigger successful panel reads'
        print('PASS: same page recovered SSE and panel reads after server restart; '+json.dumps(page.evaluate('window.__probe')))
        browser.close()
if __name__=='__main__':main()
