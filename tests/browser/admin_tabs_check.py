"""Real-browser checks for admin tabs using temporary data and synthetic identity only."""
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
from test_admin_tabs import directory_data, catalog_data


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


@contextmanager
def preview():
    data, calls = directory_data(), []
    token = 'synthetic-admin-browser-session'
    actor = {'id': 1, 'username': 'preview-owner', 'role': 'admin', 'status': 'active', 'is_superadmin': True}

    def access(path, session, payload=None, method='GET'):
        assert session == token
        calls.append((path, copy.deepcopy(payload), method))
        if path == 'admin' and method == 'GET':
            return copy.deepcopy(data)
        if path == 'me' and method == 'GET':
            return {'apps': catalog_data()}
        if path == 'admin/users/2/password/reveal' and method == 'POST':
            return {'password': 'synthetic-visible-password', 'expires_in': 30}
        if path == 'admin/requests/2/ticket' and method == 'POST':
            assert payload == {'decision': 'approved', 'note': '审批草稿保留'}
            data['requests'] = [row for row in data['requests'] if row['user_id'] != 2]
            data['audit'].append({**data['audit'][0], 'action': 'approved', 'note': payload['note']})
            return {'ok': True}
        if path == 'admin/users/2' and method == 'POST':
            assert payload == {'revision': '2', 'apps': ['ticket']}
            data['users'][1]['apps'] = ['ticket']
            return {'ok': True}
        raise AssertionError((path, payload, method))

    with tempfile.TemporaryDirectory(prefix='bifrost-admin-browser-') as directory:
        runtime = Path(directory)
        catalog = runtime / 'links.json'; catalog.write_text('[]')
        server = make_server('127.0.0.1', 0, lambda environ, start: [], request_handler=QuietHandler)
        origin = f'http://127.0.0.1:{server.server_port}'
        with patch.dict(os.environ, {'OPS_IDENTITY_URL': 'http://127.0.0.1:8787', 'BIFROST_PUBLIC_ORIGIN': origin}), \
             patch.object(IdentityClient, 'verify', side_effect=lambda session: actor if session == token else None), \
             patch.object(IdentityClient, 'access', side_effect=access):
            server.app = create_app(runtime, catalog)
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
            try:
                yield origin, token, data, calls
            finally:
                server.shutdown(); server.server_close(); worker.join(timeout=5)


def run(screenshots=None):
    if screenshots:
        screenshots.mkdir(parents=True, exist_ok=True)
    with preview() as (origin, token, data, calls), sync_playwright() as playwright:
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
        page.goto(origin + '/admin?tab=requests')
        page.wait_for_load_state('networkidle')
        panels = page.locator('[data-admin-panel]')
        expect(panels).to_have_count(3)

        def selected(tab):
            expect(page.locator('[data-admin-panel]:visible')).to_have_count(1)
            expect(page.locator(f'[data-admin-panel={tab}]')).to_be_visible()
            expect(page.locator(f'[data-admin-tab={tab}]')).to_have_attribute('aria-current', 'page')

        def switch(tab):
            page.locator(f'[data-admin-tab={tab}]').click()
            selected(tab)

        selected('requests')
        page.evaluate('window.__adminDocument = {}; window.__adminRow = document.querySelector("[data-user-id=\\"2\\"]")')
        requests = []
        page.on('request', lambda request: requests.append(request.url))
        initial_calls = len(calls)
        review = page.locator('[data-review="2/ticket"]')
        review.locator('[name=note]').fill('审批草稿保留')
        expect(page.locator('[data-review="3/ticket"] button[value=approved]')).to_be_disabled()
        switch('users')
        search = page.locator('#user-search')
        search.fill('preview-member')
        row = page.locator('[data-user-id="2"]')
        row.locator('[name=status]').select_option('disabled')
        expect(row.locator('.row-save')).to_be_enabled()
        for _ in range(3):
            switch('audit'); switch('requests')
            expect(review.locator('[name=note]')).to_have_value('审批草稿保留')
            switch('users')
            expect(search).to_have_value('preview-member')
            expect(page.locator('.user-row:visible')).to_have_count(1)
            expect(row.locator('[name=status]')).to_have_value('disabled')
        switch('requests'); switch('audit')
        page.go_back(); selected('requests')
        page.go_back(); selected('users')
        page.go_forward(); selected('requests')
        page.locator('[data-admin-tab=audit]').focus()
        page.keyboard.press('Enter'); selected('audit')
        assert page.evaluate('Boolean(window.__adminDocument) && window.__adminRow === document.querySelector("[data-user-id=\\"2\\"]")')
        assert len(calls) == initial_calls
        assert requests == [], f'Tab switches unexpectedly requested: {requests}'
        print('PASS: repeated tabs, keyboard and history: 0 requests; search, row edits and review drafts retained')

        # Revealed passwords are masked on local tab changes, including a late response.
        switch('users')
        reveal = row.locator('[data-reveal-password]')
        output = row.locator('[data-saved-password]')
        reveal.click()
        expect(output).to_have_text('synthetic-visible-password')
        switch('audit'); switch('users')
        expect(output).to_have_text('••••••')
        held = []
        pattern = '**/admin/accounts/2/password/reveal'
        page.route(pattern, lambda route: held.append(route))
        reveal.click()
        expect(reveal).to_be_disabled()
        switch('requests')
        assert len(held) == 1
        held[0].fulfill(status=200, content_type='application/json', body='{"password":"synthetic-late-password"}')
        page.unroute(pattern)
        switch('users')
        expect(reveal).to_be_enabled()
        expect(output).to_have_text('••••••')
        assert 'synthetic-late-password' not in page.locator('body').text_content()
        print('PASS: switching masks revealed passwords and ignores a late in-flight reveal')

        # A history change while a dialog is open must not leave an invisible modal blocking the page.
        switch('requests'); switch('users')
        row.get_by_role('button', name='管理权限', exact=True).click()
        expect(page.locator('#permissions-dialog')).to_be_visible()
        page.go_back(); selected('requests')
        expect(page.locator('#permissions-dialog')).not_to_be_visible()
        assert not page.evaluate('document.documentElement.classList.contains("account-modal-open")')

        # Explicit refresh gets new data and preserves the current tab.
        data['requests'][0]['reason'] = '更新后的申请用途'
        before = len(calls)
        page.locator('[data-admin-refresh]').click()
        page.wait_for_load_state('networkidle')
        selected('requests')
        expect(review.locator('.permission-note')).to_have_text('更新后的申请用途')
        assert page.evaluate('typeof window.__adminDocument') == 'undefined'
        assert len(calls) == before + 2

        # Native forms keep CSRF and submitter values when all panels share the DOM.
        review.locator('[name=note]').fill('审批草稿保留')
        review.get_by_role('button', name='同意开通').click()
        page.wait_for_load_state('networkidle')
        selected('requests')
        expect(review).to_have_count(0)
        expect(page.locator('[data-admin-tab=requests] span')).to_have_text('1')
        assert 'message=' not in page.locator('[data-admin-refresh]').get_attribute('href')
        switch('audit')
        expect(page.locator('[data-admin-panel=audit] tbody')).to_contain_text('审批草稿保留')
        switch('users')
        row.get_by_role('button', name='管理权限', exact=True).click()
        page.locator('#permissions-dialog [name=apps][value=ticket]').check()
        page.get_by_role('button', name='保存权限', exact=True).click()
        page.wait_for_load_state('networkidle')
        expect(row.locator('.user-permissions-summary')).to_contain_text('已授权 1 个')
        assert any(path == 'admin/users/2' and method == 'POST' for path, _, method in calls)
        print('PASS: explicit refresh and native CSRF approval/permission forms; counts and audit update')

        for theme in ('light', 'dark'):
            page.evaluate('(theme) => document.documentElement.dataset.theme = theme', theme)
            for width in (320, 390, 768, 1440):
                page.set_viewport_size({'width': width, 'height': 900})
                for tab in ('users', 'requests', 'audit'):
                    switch(tab)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (theme, width, tab)
                    if screenshots and width in (390, 1440):
                        page.screenshot(path=str(screenshots / f'admin-{tab}-{width}-{theme}.png'), full_page=True)

        # Disabled JavaScript still uses accurate SSR panels and ordinary links.
        fallback_context = context(java_script_enabled=False)
        fallback = fallback_context.new_page()
        fallback.goto(origin + '/admin?tab=audit')
        expect(fallback.locator('[data-admin-panel=audit]')).to_be_visible()
        expect(fallback.locator('[data-admin-panel=users]')).to_be_hidden()
        fallback.locator('[data-admin-tab=requests]').click()
        expect(fallback.locator('[data-admin-panel=requests]')).to_be_visible()
        fallback_context.close()
        page.goto(origin + '/admin?tab=invalid'); selected('users')
        switch('requests')
        data['requests'].clear()
        page.locator('[data-admin-refresh]').click()
        expect(page.locator('[data-admin-panel=requests]')).to_contain_text('当前没有待审批申请')
        expect(page.locator('[data-admin-tab=requests] span')).to_have_text('0')
        assert errors == [], errors
        print('PASS: direct links, empty queue, no-JavaScript fallback; light/dark 320–1440px; no browser errors')
        browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--screenshots', type=Path)
    run(parser.parse_args().screenshots)
