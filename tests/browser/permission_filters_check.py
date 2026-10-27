"""Check local permission filtering in Chromium against isolated synthetic identity data.

Run with a Python environment containing Flask and Playwright:
    python tests/browser/permission_filters_check.py [--screenshots /absolute/path]
"""
import argparse
import copy
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from portal import create_app
from ops_identity import COOKIE, IdentityClient
from test_permission_filters import applications


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


@contextmanager
def preview():
    records = applications()
    calls = []
    token = 'synthetic-browser-session'
    user = {'id': 81, 'username': 'filter-test', 'role': 'user', 'status': 'active'}

    def access(path, session, data=None, method='GET'):
        assert session == token
        calls.append((path, copy.deepcopy(data), method))
        if path == 'me':
            return {'apps': copy.deepcopy(records)}
        assert path == 'apps/locked/request' and method == 'POST'
        assert data == {'reason': '保留的申请草稿', 'acknowledge_shared': True}
        records[2].update(state='pending', reason=data['reason'])
        return {'ok': True}

    with tempfile.TemporaryDirectory(prefix='bifrost-permission-browser-') as directory:
        runtime = Path(directory)
        catalog = runtime / 'links.json'
        catalog.write_text('[]')
        server = make_server('127.0.0.1', 0, lambda environ, start: [], request_handler=QuietHandler)
        origin = f'http://127.0.0.1:{server.server_port}'
        with patch.dict(os.environ, {'OPS_IDENTITY_URL': 'http://127.0.0.1:8787',
                                     'BIFROST_PUBLIC_ORIGIN': origin}), \
             patch.object(IdentityClient, 'verify', side_effect=lambda session: user if session == token else None), \
             patch.object(IdentityClient, 'access', side_effect=access):
            server.app = create_app(runtime, catalog)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                yield origin, token, records, calls
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)


def run(screenshots=None):
    if screenshots:
        screenshots.mkdir(parents=True, exist_ok=True)
    with preview() as (origin, token, records, calls), sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=['--no-sandbox'])

        def context(**options):
            result = browser.new_context(**options)
            result.add_cookies([{'name': COOKIE, 'value': token, 'url': origin}])
            result.route('**/*', lambda route: route.continue_()
                         if route.request.url.startswith(origin + '/') else route.abort())
            return result

        desktop = context(viewport={'width': 1440, 'height': 1000})
        page = desktop.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(origin + '/permissions?state=pending')
        page.wait_for_load_state('networkidle')
        cards = page.locator('[data-access-id]')
        visible = page.locator('[data-access-id]:visible')
        search = page.locator('.access-search [name=q]')
        empty = page.locator('[data-permission-empty]')

        def shown(*ids):
            expect(visible).to_have_count(len(ids))
            assert visible.evaluate_all('(cards) => cards.map(card => card.dataset.accessId)') == list(ids)

        def tab(state):
            page.locator(f'[data-permission-filter={state}]').click()
            expect(page.locator(f'[data-permission-filter={state}]')).to_have_attribute('aria-current', 'page')

        expect(cards).to_have_count(6)
        shown('waiting')
        page.evaluate('window.__filterDocument = {}; window.__draftCard = document.getElementById("app-locked")')
        requests = []
        page.on('request', lambda request: requests.append(request.url))
        initial_calls = len(calls)
        for _ in range(3):
            tab('available'); shown('opened')
            tab('requestable'); shown('locked', 'rejected', 'revoked')
            tab('pending'); shown('waiting')
            tab('all'); shown(*(item['id'] for item in records))

        # Hidden cards keep their live form fields and native disclosure state.
        tab('requestable')
        draft = page.locator('#app-locked')
        draft.locator('summary').click()
        draft.locator('[name=reason]').fill('保留的申请草稿')
        draft.locator('[name=ack]').check()
        tab('pending'); tab('requestable')
        expect(draft.locator('details')).to_have_attribute('open', '')
        expect(draft.locator('[name=reason]')).to_have_value('保留的申请草稿')
        expect(draft.locator('[name=ack]')).to_be_checked()
        assert page.evaluate('window.__draftCard === document.getElementById("app-locked")')

        search.fill('aLPHa'); shown('locked')
        search.press('Enter'); shown('locked')
        assert 'state=requestable' in page.url and 'q=aLPHa' in page.url
        tab('available'); shown()
        expect(empty).to_be_visible()
        page.go_back(); shown('locked')
        expect(search).to_have_value('aLPHa')
        page.go_forward(); shown()
        page.locator('[data-permission-reset]').click()
        shown(*(item['id'] for item in records))
        expect(search).to_have_value('')
        search.fill('小红书'); shown('locked')
        search.fill('WAITING'); shown('waiting')
        search.fill('不存在的应用'); shown()
        expect(empty).to_be_visible()
        page.locator('[data-permission-reset]').click()

        # IME composition should not change the list until the text is committed.
        search.dispatch_event('compositionstart')
        search.fill('小红书')
        shown(*(item['id'] for item in records))
        search.dispatch_event('compositionend')
        shown('locked')
        assert page.evaluate('Boolean(window.__filterDocument)')
        assert len(calls) == initial_calls
        assert requests == [], f'Local filters unexpectedly made requests: {requests}'
        print('PASS: repeated filters, search, IME, history and drafts: 0 requests, same document')

        search.fill('')
        tab('available')
        for width in (1440, 768, 390, 320):
            page.set_viewport_size({'width': width, 'height': 900})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}px'
            if screenshots and width in (1440, 390):
                page.screenshot(path=str(screenshots / f'permissions-{width}.png'), full_page=True)
        page.set_viewport_size({'width': 1440, 'height': 1000})

        # Explicit refresh revalidates against central data and preserves the filter.
        search.fill('应用')
        records[1]['state'] = 'available'
        before_refresh = len(calls)
        page.locator('[data-permission-refresh]').click()
        page.wait_for_load_state('networkidle')
        shown('opened', 'waiting')
        expect(search).to_have_value('应用')
        assert page.evaluate('typeof window.__filterDocument') == 'undefined'
        assert len(calls) == before_refresh + 1

        # The existing POST/CSRF/redirect flow still submits the retained fields.
        search.fill('')
        tab('pending'); shown()
        expect(empty.locator('h2')).to_have_text('暂时没有审批中的申请')
        tab('requestable')
        draft.locator('summary').click()
        draft.locator('[name=reason]').fill('保留的申请草稿')
        draft.locator('[name=ack]').check()
        draft.get_by_role('button', name='提交申请').click()
        page.wait_for_url('**/permissions?app=locked**')
        expect(draft.locator('.permission-state')).to_have_text('审批中')
        assert any(path == 'apps/locked/request' and method == 'POST' for path, _, method in calls)
        assert 'message=' not in page.locator('[data-permission-refresh]').get_attribute('href')
        tab('pending'); shown('locked')
        print('PASS: explicit refresh sees changed permissions; real CSRF form submission succeeds')

        # Direct links and disabled JavaScript keep server-rendered visibility and GET links.
        no_js = context(java_script_enabled=False)
        fallback = no_js.new_page()
        fallback.goto(origin + '/permissions?state=requestable&q=未通过')
        expect(fallback.locator('[data-access-id]:visible')).to_have_count(1)
        expect(fallback.locator('#app-rejected')).to_be_visible()
        fallback.locator('[data-permission-filter=available]').click()
        expect(fallback.locator('[data-access-id]:visible')).to_have_count(0)
        expect(fallback.locator('[data-permission-empty]')).to_be_visible()
        no_js.close()
        assert errors == [], errors
        print('PASS: 320–1440px layouts, direct links, no-JavaScript fallback; no browser errors')
        browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--screenshots', type=Path)
    run(parser.parse_args().screenshots)
