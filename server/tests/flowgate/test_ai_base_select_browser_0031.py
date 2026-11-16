"""v02gui.0031 D: the AI model pickers in real Chromium (no application server).

The fixture loads the real theme.css, aiBaseSelect.css and the real scoped styles of Select.vue /
AiRoleSelect.vue / AiRoleMatrix.vue (scoped attribute re-applied so specificity matches the app), with the
same markup the components render. It measures what is drawn and drives the open list with real input.
It does not mount Vue or call the API (the Vitest suites and the opt-in 0120 browser test do).
"""
import glob
import json
import os
import platform
import re
from pathlib import Path

import pytest

playwright = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[3]
GUI = ROOT / 'client_gui/src'
SCOPE = '[data-v-fx]'


def _installed_chromium():
    explicit = os.environ.get('FG_CHROMIUM_PATH')
    if explicit:
        return explicit
    found = sorted(glob.glob('C:/Users/*/AppData/Local/ms-playwright/chromium-*/chrome-win*/chrome.exe'))
    return found[-1] if found else None


def _scoped_css(path):
    source = path.read_text(encoding='utf-8')
    css = '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', source, re.S))
    css = css.replace(':deep(', ':is(')

    def add(match):
        indent, selectors = match.group(1), match.group(2)
        out = []
        for sel in selectors.split(','):
            sel = sel.strip()
            m = re.match(r'^(.*?)((?:::[\w-]+(?:\([^)]*\))?)*)$', sel)
            out.append(m.group(1) + SCOPE + m.group(2))
        return indent + ', '.join(out) + ' {'

    return re.sub(r'(?m)^([ \t]*)([^@{}\n/\s][^{}\n]*?)\s*\{', add, css)


@pytest.fixture(scope='module')
def browser():
    with playwright.sync_playwright() as driver:
        # Playwright's headless launch adds --hide-scrollbars; the scrollbar is what is being measured, so keep it.
        options = {'headless': True, 'ignore_default_args': ['--hide-scrollbars']}
        try:
            instance = driver.chromium.launch(**options)
        except Exception:
            executable = _installed_chromium()
            if not executable:
                raise
            instance = driver.chromium.launch(executable_path=executable, **options)
        yield instance
        instance.close()


def _options(count):
    rows = [('not_used', '사용하지 않음')] + [(f'assigned:p{i}', f'Provider model {i:02d}') for i in range(count)]
    return ''.join(f'<option value="{v}"{" disabled" if v == "assigned:p3" else ""}>{l}</option>' for v, l in rows)


def _page_html(count=40):
    theme = (GUI / 'app/styles/theme.css').read_text(encoding='utf-8')
    base = (GUI / 'features/ai/aiBaseSelect.css').read_text(encoding='utf-8')
    select = _scoped_css(GUI / 'components/ui/Select.vue')
    role = _scoped_css(GUI / 'features/ai/roles/AiRoleSelect.vue')
    matrix = _scoped_css(GUI / 'features/ai/groups/AiRoleMatrix.vue')
    opts = _options(count)
    body = f"""
    <div id="role" style="width:260px;margin:16px">
      <span class="ai-role-select" {SCOPE[1:-1]}>
        <select class="ui-select ai-base-select" id="ai-role" aria-label="실행 AI" {SCOPE[1:-1]}>{opts}</select>
      </span>
    </div>
    <div id="matrix" style="width:200px;margin:16px">
      <select class="role-select ai-base-select" id="matrix-select" data-role="execution" {SCOPE[1:-1]}>
        <option value="inherit">공용 · Model One</option>{opts}<option value="">미설정</option></select>
      <select class="role-select ai-base-select" id="matrix-disabled" disabled {SCOPE[1:-1]}><option>공용 · Model One</option></select>
    </div>
    <div id="plain" style="width:260px;margin:16px">
      <select class="ui-select" id="plain-select" {SCOPE[1:-1]}>{opts}</select>
    </div>
    <p><input id="after" value="next"></p>
    """
    script = """
    window.events = [];
    for (const el of document.querySelectorAll('select')) {
      el.addEventListener('change', () => window.events.push([el.id, el.value]));
    }
    """
    return f'<!doctype html><meta charset="utf-8"><style>{theme}\nbody{{background:var(--fg2-bg);color:var(--fg2-text)}}</style><style>{base}</style><style>{select}</style><style>{role}</style><style>{matrix}</style>{body}<script>{script}</script>'


def _lum(rgb):
    def chan(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _rgb(text):
    return tuple(int(float(v)) for v in re.findall(r'[\d.]+', text)[:3])


@pytest.fixture
def page(browser):
    page = browser.new_page(viewport={'width': 800, 'height': 700})
    page.set_content(_page_html())
    yield page
    page.close()


def _supported(browser, page):
    return page.evaluate("CSS.supports('appearance', 'base-select')")


def test_environment_is_recorded(browser, page, record_property):
    info = {'chromium': browser.version, 'os': platform.platform(), 'base_select': _supported(browser, page)}
    record_property('environment', json.dumps(info))
    print('\nCHROMIUM_ENV', json.dumps(info))
    assert info['chromium']


def test_only_the_opted_in_selects_use_base_select(browser, page):
    if not _supported(browser, page):
        pytest.skip(f'Chromium {browser.version} has no appearance: base-select')
    result = page.evaluate("""() => Object.fromEntries(['ai-role', 'matrix-select', 'matrix-disabled', 'plain-select'].map(id => {
        const cs = getComputedStyle(document.getElementById(id));
        return [id, {appearance: cs.appearance, height: cs.height, border: cs.borderTopColor, color: cs.color}];
    }))""")
    print('\nCOMPUTED', json.dumps(result, ensure_ascii=False))
    assert result['ai-role']['appearance'] == 'base-select'
    assert result['matrix-select']['appearance'] == 'base-select'
    assert result['matrix-disabled']['appearance'] == 'base-select'
    assert result['plain-select']['appearance'] == 'none'


def test_picker_style_scrollbar_and_contrast(browser, page):
    if not _supported(browser, page):
        pytest.skip(f'Chromium {browser.version} has no appearance: base-select')
    picker = page.evaluate("""() => {
        const cs = getComputedStyle(document.getElementById('ai-role'), '::picker(select)');
        return {appearance: cs.appearance, scrollbarWidth: cs.scrollbarWidth, scrollbarColor: cs.scrollbarColor,
                maxHeight: cs.maxHeight, background: cs.backgroundColor, color: cs.color, overflowY: cs.overflowY,
                border: cs.borderTopWidth + ' ' + cs.borderTopColor};
    }""")
    print('\nPICKER_COMPUTED', json.dumps(picker, ensure_ascii=False))
    assert picker['appearance'] == 'base-select'
    assert picker['scrollbarWidth'] == 'auto'
    assert picker['overflowY'] == 'auto'
    thumb, track = re.findall(r'rgb[a]?\([^)]*\)', picker['scrollbarColor'])[:2]
    ratio = _contrast(_rgb(thumb), _rgb(track))
    print('THUMB_TRACK', thumb, track, round(ratio, 2))
    assert ratio >= 3, ratio  # WCAG 1.4.11 non-text contrast
    page.locator('#ai-role').click()
    assert page.evaluate("document.getElementById('ai-role').matches(':open')")
    shot = page.screenshot()
    (Path(os.environ.get('FG_BROWSER_ARTIFACTS', '.')) / 'ai_base_select_open.png').write_bytes(shot) if os.environ.get('FG_BROWSER_ARTIFACTS') else None
    first = page.locator('#ai-role option').nth(0).bounding_box()
    last_visible = page.evaluate("""() => [...document.querySelectorAll('#ai-role option')].map(o => o.getBoundingClientRect()).filter(r => r.bottom > 0).length""")
    print('FIRST_OPTION', first, 'ROWS', last_visible)
    assert first and first['height'] > 0
    page.keyboard.press('Escape')


def _scroll_state(page):
    return page.evaluate("""() => {
        const o = document.querySelectorAll('#ai-role option');
        return {first: o[0].getBoundingClientRect().top, last: o[o.length - 1].getBoundingClientRect().top};
    }""")


def test_wheel_keyboard_and_selection_survive(browser, page):
    if not _supported(browser, page):
        pytest.skip(f'Chromium {browser.version} has no appearance: base-select')
    sel = page.locator('#ai-role')
    sel.click()
    before = _scroll_state(page)
    box = page.locator('#ai-role option').nth(2).bounding_box()
    page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
    page.mouse.wheel(0, 400)
    page.wait_for_timeout(300)
    after = _scroll_state(page)
    print('\nWHEEL', before, after)
    assert after['first'] < before['first'] - 50, (before, after)  # the list scrolled
    assert page.evaluate("document.getElementById('ai-role').matches(':open')")
    # Keyboard: arrow down through the (long) list, Enter chooses; the native change event fires once.
    page.evaluate("window.events.length = 0")
    for _ in range(12):
        page.keyboard.press('ArrowDown')
    page.keyboard.press('Enter')
    page.wait_for_timeout(200)
    value = sel.input_value()
    events = page.evaluate("window.events")
    print('KEYBOARD', value, events)
    assert value.startswith('assigned:p') and value != 'assigned:p3'  # a disabled option is skipped
    assert events == [['ai-role', value]]
    assert not page.evaluate("document.getElementById('ai-role').matches(':open')")
    assert page.evaluate("document.activeElement.id") == 'ai-role'


def test_escape_keeps_value_and_tab_moves_on(browser, page):
    if not _supported(browser, page):
        pytest.skip(f'Chromium {browser.version} has no appearance: base-select')
    sel = page.locator('#matrix-select')
    sel.select_option('inherit')
    page.evaluate("window.events.length = 0")
    sel.click()
    for _ in range(5):
        page.keyboard.press('ArrowDown')
    page.keyboard.press('Escape')
    page.wait_for_timeout(150)
    assert sel.input_value() == 'inherit'
    assert page.evaluate("window.events") == []
    assert page.evaluate("document.activeElement.id") == 'matrix-select'
    page.keyboard.press('Tab')  # the next control (the disabled select is skipped)
    assert page.evaluate("document.activeElement.id") == 'plain-select'
    # inherit / not_used / unset values are kept as plain option values.
    for value in ('not_used', 'assigned:p5', '', 'inherit'):
        sel.select_option(value)
        assert sel.input_value() == value


def test_disabled_select_does_not_open(browser, page):
    if not _supported(browser, page):
        pytest.skip(f'Chromium {browser.version} has no appearance: base-select')
    page.locator('#matrix-disabled').click(force=True)
    assert not page.evaluate("document.getElementById('matrix-disabled').matches(':open')")
    assert page.evaluate("window.events") == []


def test_thumb_can_be_found_and_dragged(browser, page):
    if not _supported(browser, page):
        pytest.skip(f'Chromium {browser.version} has no appearance: base-select')
    try:
        from PIL import Image
    except ImportError:
        pytest.skip('Pillow is not installed: scrollbar pixel measurement skipped')
    import io
    page.locator('#ai-role').click()
    page.wait_for_timeout(300)
    geometry = page.evaluate("""() => {
        const o = [...document.querySelectorAll('#ai-role option')].map(x => x.getBoundingClientRect());
        const s = document.getElementById('ai-role').getBoundingClientRect();
        return {optionRight: Math.max(...o.map(x => x.right)), selectRight: s.right, top: o[0].top};
    }""")
    colors = re.findall(r'rgb[a]?\([^)]*\)', page.evaluate("getComputedStyle(document.getElementById('ai-role'), '::picker(select)').scrollbarColor"))
    thumb, track = _rgb(colors[0]), _rgb(colors[1])
    image = Image.open(io.BytesIO(page.screenshot())).convert('RGB')
    y = int(geometry['top'] + 60)

    def near(pixel, color):
        return max(abs(pixel[i] - color[i]) for i in range(3)) <= 12

    span = range(int(geometry['optionRight']) - 2, int(geometry['selectRight']))
    thumbs = [x for x in span if near(image.getpixel((x, y)), thumb)]
    tracks = [x for x in span if near(image.getpixel((x, y)), track)]
    print('\nSCROLLBAR options_right', geometry['optionRight'], 'select_right', geometry['selectRight'],
          'thumb_px', (thumbs[0], thumbs[-1], len(thumbs)) if thumbs else None, 'track_px', len(tracks))
    assert thumbs, 'no thumb-colored pixels inside the picker (scrollbar not drawn?)'
    width = thumbs[-1] - thumbs[0] + 1
    assert width >= 8, width  # a graspable thumb, not the 3-4px thin bar
    x = (thumbs[0] + thumbs[-1]) // 2
    before = _scroll_state(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + 100, steps=8)
    page.mouse.up()
    page.wait_for_timeout(200)
    after = _scroll_state(page)
    print('DRAG', before, after)
    assert after['first'] < before['first'] - 20, (before, after)
    assert page.evaluate("document.getElementById('ai-role').matches(':open')")


@pytest.mark.parametrize('viewport,scale', [((320, 640), 1), ((640, 360), 2), ((800, 300), 1)])
def test_picker_stays_inside_narrow_zoomed_and_short_screens(browser, viewport, scale):
    page = browser.new_page(viewport={'width': viewport[0], 'height': viewport[1]}, device_scale_factor=scale)
    try:
        page.set_content(_page_html())
        if not _supported(browser, page):
            pytest.skip(f'Chromium {browser.version} has no appearance: base-select')
        page.locator('#ai-role').click()
        page.wait_for_timeout(300)
        info = page.evaluate("""() => {
            const o = [...document.querySelectorAll('#ai-role option')].map(x => x.getBoundingClientRect());
            const cs = getComputedStyle(document.getElementById('ai-role'), '::picker(select)');
            const sel = document.getElementById('ai-role').getBoundingClientRect();
            return {left: o[0].left, right: o[0].right, top: o[0].top, content: o[o.length - 1].bottom - o[0].top,
                    maxHeight: parseFloat(cs.maxHeight), selLeft: sel.left, selRight: sel.right, vw: innerWidth, vh: innerHeight};
        }""")
        # The picker box: 1px border + 4px padding around the options; its height is capped by max-height.
        picker_top = info['top'] - 5
        picker_height = min(info['maxHeight'], info['content'] + 10)
        print('\nVIEWPORT', viewport, scale, json.dumps(info), 'picker', round(picker_top, 1), round(picker_height, 1))
        assert info['maxHeight'] <= 0.6 * viewport[1] + 0.5 or info['maxHeight'] == 320
        assert picker_top >= 0 and picker_top + picker_height <= viewport[1] + 1, (picker_top, picker_height)
        assert info['left'] >= 0 and info['right'] <= viewport[0]
        assert page.evaluate("document.documentElement.scrollWidth") <= viewport[0]
        page.keyboard.press('ArrowDown')
        page.keyboard.press('Enter')
        assert page.locator('#ai-role').input_value().startswith(('assigned:', 'not_used'))
    finally:
        page.close()
