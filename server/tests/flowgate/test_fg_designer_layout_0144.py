"""0144 T0013/T0014 CSS layout fixtures in Chromium, without an application server.

These test the actual component styles with representative markup. They do not claim
to cover Vue mounting, drag events or memo editing (the existing Vitest suites do).
"""
import glob
import html
import os
import re
from pathlib import Path

import pytest

playwright = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[3]
DESIGNER = ROOT / 'client_gui/src/features/designer'
ELLIPSIS = '\u2026'
CARD_WIDTH = 135
GAP = 7


def styles(path):
    source = path.read_text(encoding='utf-8')
    css = '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', source, re.S))
    # Scoped selectors have the same meaning inside this isolated fixture.
    return css.replace(':deep(', ':is(')


def _installed_chromium():
    """An installed Chromium when the default Playwright cache has none (isolated profiles)."""
    explicit = os.environ.get('FG_CHROMIUM_PATH')
    if explicit:
        return explicit
    found = sorted(glob.glob('C:/Users/*/AppData/Local/ms-playwright/chromium-*/chrome-win*/chrome.exe'))
    return found[-1] if found else None


@pytest.fixture(scope='module')
def browser():
    with playwright.sync_playwright() as driver:
        try:
            instance = driver.chromium.launch(headless=True)
        except Exception:
            executable = _installed_chromium()
            if not executable:
                raise
            instance = driver.chromium.launch(headless=True, executable_path=executable)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    page = browser.new_page(viewport={'width': 1200, 'height': 900})
    yield page
    page.close()


def test_palette_cards_keep_a_fixed_width_and_wrap_like_text(page):
    css = styles(DESIGNER / 'palette/PalettePanel.vue') + styles(DESIGNER / 'palette/PaletteCard.vue')
    cards = ''.join(f'<button class="palette-card" draggable="true" data-order="{i}">{i}</button>' for i in range(12))
    sections = ''.join(
        f'<section class="designer-palette-section" data-category="{category}"><div class="designer-palette-grid">{cards}</div></section>'
        for category in ('design', 'work', 'control')
    )
    page.set_content(f'<style>{css}</style><div class="designer-palette">{sections}</div>')
    # The panel content is the width minus the 11px padding on both sides (300px panel -> 278px).
    for width in (300, 320, 421, 450, 600, 900, 300, 180, 100, 300):
        page.locator('.designer-palette').evaluate('(el, width) => el.style.width = width + "px"', width)
        result = page.locator('.designer-palette-grid').evaluate_all(
            """grids => grids.map(grid => ({
            display: getComputedStyle(grid).display,
            wrap: getComputedStyle(grid).flexWrap,
            gap: getComputedStyle(grid).columnGap + ' ' + getComputedStyle(grid).rowGap,
            width: grid.clientWidth,
            overflow: grid.scrollWidth > grid.clientWidth,
            cards: [...grid.children].map(card => {
                const r = card.getBoundingClientRect(), g = grid.getBoundingClientRect();
                return {x:r.x - g.x, y:r.y - g.y, width:r.width, height:r.height, order:card.dataset.order, draggable:card.draggable};
            })
        }))"""
        )
        content = width - 22
        per_row = max(1, (content + GAP) // (CARD_WIDTH + GAP))
        for grid in result:
            assert (grid['display'], grid['wrap']) == ('flex', 'wrap')
            assert grid['gap'] == f'{GAP}px {GAP}px' and not grid['overflow'], (width, grid)
            for i, card in enumerate(grid['cards']):
                assert card['order'] == str(i) and card['draggable'] and card['height'] == 52
                # The card itself never stretches with the panel (only a panel narrower than one card shrinks it).
                assert abs(card['width'] - min(CARD_WIDTH, content)) < 0.6, (width, i, card)
                # Cards fill each row from the left and the next one wraps to the next row.
                assert abs(card['x'] - (i % per_row) * (CARD_WIDTH + GAP)) < 0.6, (width, i, card)
                assert abs(card['y'] - (i // per_row) * (52 + GAP)) < 0.6, (width, i, card)


def _title_preview(title, limit=30):
    characters = list(title)
    return ''.join(characters[:limit]) + ELLIPSIS if len(characters) > limit else title


def test_memo_preview_limit_is_thirty_characters():
    model = (ROOT / 'client_gui/src/features/designer/inspector/features/cardMemo/cardMemoModel.ts').read_text(encoding='utf-8')
    assert re.search(r'CARD_MEMO_TITLE_PREVIEW_MAX\s*=\s*30\b', model)
    assert _title_preview('a' * 30) == 'a' * 30
    assert _title_preview('a' * 31) == 'a' * 30 + ELLIPSIS


@pytest.mark.parametrize('badge_text', [None, 'AI 참조', 'AI refers', 'AI 参照'])
def test_long_memo_title_is_visible_with_its_ellipsis_at_every_width_and_actions_stay_inside_row(page, badge_text):
    """TR0014 rev2: not only the DOM text but what is drawn - the title and its ellipsis must have room beside the
    [AI ref] badge in the narrowest (236px list) Inspector, and nothing may overlap the badge or the icons."""
    css = (
        styles(DESIGNER / 'inspector/parts/InspectorSlotRow.vue')
        + styles(DESIGNER / 'inspector/features/cardMemo/CardMemoSection.vue')
        + styles(ROOT / 'client_gui/src/components/ui/IconButton.vue')
    )
    title = '記憶VeryLongUnbrokenTitle\U0001F600' * 8
    shown = _title_preview(title)
    assert len(shown) == 31 and shown.endswith(ELLIPSIS)
    badge = f'<span class="inspector-slot-state is-info">{badge_text}</span>' if badge_text else ''
    extra = ' has-ai-ref' if badge_text else ''
    buttons = ''.join(f'<button class="ui-icon-btn ui-icon-btn--sm" aria-label="{name}">✎</button>' for name in ('open', 'edit', 'remove'))
    markup = (
        '<div class="frame"><section class="inspector-card-memo"><div class="inspector-card-memo-list">'
        f'<div class="inspector-slot inspector-card-memo-item{extra}">'
        f'<button class="inspector-slot-name">메모</button><span class="inspector-slot-value">{badge}'
        f'<span class="inspector-slot-summary" title="{html.escape(title)}">{html.escape(shown)}</span></span>'
        f'<span class="inspector-slot-buttons">{buttons}</span></div></div></section></div>'
    )
    page.set_content(f'<style>.frame{{box-sizing:border-box;padding:14px;font-family:Arial}}{css}</style>{markup}')
    for width in (264, 292, 320, 388, 500, 900, 1600, 264):
        page.locator('.frame').evaluate('(el, width) => el.style.width = width + "px"', width)
        result = page.locator('.inspector-slot').evaluate(
            """row => {
            const box = e => { const r = e.getBoundingClientRect(); return {left:r.left, right:r.right, top:r.top, bottom:r.bottom, width:r.width}; };
            const summary = row.querySelector('.inspector-slot-summary'), badge = row.querySelector('.inspector-slot-state');
            const walker = document.createTreeWalker(summary, NodeFilter.SHOW_TEXT), node = walker.nextNode();
            const range = document.createRange(); range.setStart(node, 0); range.setEnd(node, 1);
            const first = range.getBoundingClientRect(), s = box(summary);
            return {
                row:box(row), overflow:row.scrollWidth > row.clientWidth, list:row.parentElement.getBoundingClientRect().width,
                text:summary.textContent, tooltip:summary.getAttribute('title'),
                ellipsis:getComputedStyle(summary).textOverflow, nowrap:getComputedStyle(summary).whiteSpace,
                summary:s, clipped:summary.scrollWidth > summary.clientWidth, firstChar:{left:first.left, right:first.right, width:first.width},
                badge:badge ? box(badge) : null,
                buttons:[...row.querySelectorAll('.ui-icon-btn')].map(box),
                hasTrash: !!row.querySelector('[aria-label=remove]')
            };
        }"""
        )
        assert not result['overflow'], (width, result)
        # Whatever the width, the text is the 30-character preview; the whole title stays in the tooltip.
        assert result['text'] == shown and result['tooltip'] == title, (width, result)
        assert (result['ellipsis'], result['nowrap']) == ('ellipsis', 'nowrap')
        assert len(result['buttons']) == 3
        row = result['row']
        for button in result['buttons']:
            assert button['width'] >= 26
            assert row['left'] <= button['left'] < button['right'] <= row['right']
        # Visible, not just present: the title has real room (several characters and the ellipsis) inside the row,
        # its first character is drawn inside its box, and it overlaps neither the badge nor the icons.
        summary = result['summary']
        # With the badge on a narrow list the title sits on its own second row (whole line width); without it, it keeps
        # the single row and the room beside name and icons (a 236px list: 54px, several characters and the ellipsis).
        need = 100 if (badge_text and result['list'] <= 360) else 40
        assert summary['width'] >= need and summary['right'] <= row['right'], (width, result)
        first = result['firstChar']
        assert first['width'] > 0 and summary['left'] <= first['left'] and first['right'] <= summary['right'] + 0.5, (width, result)
        for other in [result['badge'], *result['buttons']]:
            if other:
                apart = summary['bottom'] <= other['top'] + 0.5 or other['bottom'] <= summary['top'] + 0.5
                apart = apart or summary['right'] <= other['left'] + 0.5 or other['right'] <= summary['left'] + 0.5
                assert apart, ('title overlaps badge/button', width, result)
        if result['clipped']:
            # The CSS ellipsis (text-overflow) only has something to draw when the box itself is visible.
            assert summary['width'] > 0
