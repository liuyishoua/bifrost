"""Real browser regression with temporary accounts and blocked external sites.

Run using a Python environment with Flask, Waitress and Playwright installed:
    python tests/browser/grouped_directory_check.py [--screenshots /absolute/path]
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from portal import create_app, password_hash


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


@contextmanager
def preview():
    with tempfile.TemporaryDirectory(prefix='bifrost-browser-') as directory:
        runtime = Path(directory)
        entries = json.loads((ROOT / 'links.json').read_text())
        entries.append({'id': 'pending-project', 'name': '待部署示例', 'enabled': False, 'url': ''})
        catalog = runtime / 'links.json'
        catalog.write_text(json.dumps(entries, ensure_ascii=False))
        # Explicitly disable unified identity only for this isolated preview.
        environment = {key: value for key, value in os.environ.items() if key != 'OPS_IDENTITY_URL'}
        with patch.dict(os.environ, environment, clear=True):
            app = create_app(runtime, catalog)
        server = make_server('127.0.0.1', 0, app, request_handler=QuietHandler)
        origin = f'http://127.0.0.1:{server.server_port}'
        password = secrets.token_urlsafe(20)
        with sqlite3.connect(runtime / 'portal.sqlite3') as connection:
            connection.execute("INSERT INTO users(username,password_hash,role,status,created_at) VALUES(?,?,'user','active',0)",
                               ('preview-user', password_hash(password)))
        # Local form posts must match the origin captured by create_app.
        with patch.dict(os.environ, {**environment, 'BIFROST_PUBLIC_ORIGIN': origin}, clear=True):
            app = create_app(runtime, catalog)
        server.app = app
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield origin, password, entries, catalog
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


def run(screenshots):
    if screenshots:
        screenshots.mkdir(parents=True, exist_ok=True)
    with preview() as (origin, password, entries, catalog), sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('CHROME_PATH') or shutil.which('google-chrome'), args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1160})
        context.add_init_script("localStorage.setItem('workspace-theme', 'dark')")
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(origin + '/login')
        page.locator('[name=username]').fill('preview-user')
        page.locator('[name=password]').fill(password)
        page.locator('.auth-panel button[type=submit], .auth-panel button:not([type])').click()
        page.wait_for_url(origin + '/')
        page.wait_for_load_state('networkidle')
        requests = []
        page.on('request', lambda request: requests.append(request.url))
        grouped = page.locator('[data-grouped-directory]')
        flat = page.locator('[data-flat-directory]')
        search = page.locator('[name=q]')
        details = page.locator('[data-operations-details]')
        xhs = page.locator('[data-project-id=xhs-all-in-one]')
        multica = page.locator('[data-project-id=multica]')
        all_rows = page.locator('[data-project-id]')
        original_ids = [entry['id'] for entry in entries]
        expect(grouped).to_be_visible()
        expect(flat).to_be_hidden()
        expect(details).not_to_have_attribute('open', '')
        expect(xhs).to_be_hidden()
        expect(multica).to_be_visible()
        expect(multica.locator('xpath=ancestor::section')).to_have_attribute('data-directory-group', 'ai')
        expect(all_rows).to_have_count(len(entries))
        if screenshots:
            page.screenshot(path=str(screenshots / 'bifrost-grouped-desktop.png'), full_page=True, animations='disabled')

        # Native disclosure and direct, safe destinations.
        details.locator('summary').click()
        expect(xhs).to_be_visible()
        expect(xhs.locator('a')).to_have_attribute('target', '_blank')
        expect(xhs.locator('a')).to_have_attribute('rel', 'noopener noreferrer')
        assert xhs.locator('a').get_attribute('href') == next(entry['url'] for entry in entries if entry['id'] == 'xhs-all-in-one')
        details.locator('summary').click()
        expect(xhs).to_be_hidden()

        # Search finds collapsed entries by Chinese name and opens the group.
        search.fill('小红书')
        expect(xhs).to_be_visible()
        expect(details).to_have_attribute('open', '')
        expect(multica).to_be_hidden()
        expect(page.locator('[data-result-count]')).to_contain_text('找到')
        search.fill('')
        expect(xhs).to_be_hidden()
        expect(multica).to_be_visible()

        # Layout switches move existing rows without duplicates or reordering.
        page.locator('[data-view=flat]').click()
        expect(flat).to_be_visible()
        expect(grouped).to_be_hidden()
        expect(flat.locator('[data-project-id]')).to_have_count(len(entries))
        assert flat.locator('[data-project-id]').evaluate_all('(rows) => rows.map(row => row.dataset.projectId)') == original_ids
        page.locator('.categories [data-category="运营工作区"]').click()
        expect(page.locator('[data-project-id=account-maintenance]')).to_be_visible()
        expect(multica).to_be_hidden()
        search.fill('Multica')
        expect(multica).to_be_visible()
        expect(page.locator('[name=category]')).to_have_value('')
        expect(page.locator('[data-result-count]')).to_contain_text('找到 1 /')
        search.fill('不存在的入口')
        expect(page.locator('[data-empty]')).to_be_visible()
        page.locator('[data-reset]').click()
        expect(page.locator('[data-empty]')).to_be_hidden()
        expect(flat.locator('[data-project-id]:visible')).to_have_count(len(entries))
        expect(page.locator('[data-project-id=pending-project] a')).to_have_count(0)
        page.locator('[data-view=grouped]').click()
        expect(grouped).to_be_visible()
        page.go_back()
        expect(flat).to_be_visible()
        page.go_forward()
        expect(grouped).to_be_visible()
        assert requests == [], f'Interactions made network requests: {requests}'

        # First loads, reloads, and no-JavaScript fallbacks honor URL state.
        page.goto(origin + '/?view=flat&q=小红书')
        expect(flat).to_be_visible()
        expect(xhs).to_be_visible()
        expect(multica).to_be_hidden()
        page.reload()
        expect(xhs).to_be_visible()
        page.goto(origin + '/?category=工具')
        expect(flat).to_be_visible()
        expect(page.locator('[data-project-id=account-maintenance]')).to_be_visible()
        page.locator('[data-view=grouped]').click()
        expect(grouped).to_be_visible()
        page.reload()
        expect(grouped).to_be_visible()
        expect(page.locator('[data-project-id=account-maintenance]')).to_be_visible()
        page.goto(origin + '/?q=小红书')
        expect(grouped).to_be_visible()
        expect(xhs).to_be_visible()
        page.goto(origin + '/?view=invalid')
        expect(grouped).to_be_visible()
        expect(all_rows).to_have_count(len(entries))
        pending = page.locator('[data-project-id=pending-project]')
        expect(pending.locator('xpath=ancestor::section')).to_have_attribute('data-directory-group', 'independent')

        # Theme and narrow screens, both collapsed and expanded.
        page.goto(origin + '/')
        page.get_by_role('button', name='切换到白天模式').click()
        expect(page.locator('html')).to_have_attribute('data-theme', 'light')
        if screenshots:
            page.screenshot(path=str(screenshots / 'bifrost-grouped-light.png'), full_page=True, animations='disabled')
        page.get_by_role('button', name='切换到黑夜模式').click()
        for width in (390, 320):
            page.set_viewport_size({'width': width, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}px'
            details.locator('summary').click()
            expect(xhs).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Expanded overflow at {width}px'
            details.locator('summary').click()
            if screenshots and width == 390:
                page.screenshot(path=str(screenshots / 'bifrost-grouped-mobile.png'), full_page=True, animations='disabled')
        assert not errors, errors

        no_js = browser.new_context(java_script_enabled=False)
        no_js.add_cookies(context.cookies())
        fallback = no_js.new_page()
        fallback.goto(origin + '/')
        expect(fallback.locator('[data-grouped-directory]')).to_be_visible()
        fallback.locator('.operations-details summary').click()
        expect(fallback.locator('[data-project-id=xhs-all-in-one]')).to_be_visible()
        fallback.goto(origin + '/?view=flat&q=小红书')
        expect(fallback.locator('[data-flat-directory]')).to_be_visible()
        expect(fallback.locator('[data-project-id=xhs-all-in-one]')).to_be_visible()
        expect(fallback.locator('[data-project-id=multica]')).to_be_hidden()
        fallback.locator('.categories [data-category="AI 与协作"]').click()
        expect(fallback.locator('[data-project-id=multica]')).to_be_visible()
        expect(fallback.locator('[data-project-id=xhs-all-in-one]')).to_be_hidden()
        fallback.locator('[data-view=grouped]').click()
        expect(fallback.locator('[data-grouped-directory]')).to_be_visible()

        # Deliver previews of the real catalog, without the disabled test fixture.
        if screenshots:
            catalog.write_text(json.dumps(entries[:-1], ensure_ascii=False))
            page.set_viewport_size({'width': 1440, 'height': 1160})
            page.goto(origin + '/')
            page.screenshot(path=str(screenshots / 'bifrost-grouped-desktop.png'), full_page=True, animations='disabled')
            page.get_by_role('button', name='切换到白天模式').click()
            page.screenshot(path=str(screenshots / 'bifrost-grouped-light.png'), full_page=True, animations='disabled')
            page.get_by_role('button', name='切换到黑夜模式').click()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(screenshots / 'bifrost-grouped-mobile.png'), full_page=True, animations='disabled')
        browser.close()
        print('PASS: grouped/flat, disclosure, global search, categories, history, URL reloads, safe links, disabled/new entries, zero interaction requests, themes, 320/390px layouts, no-JS fallback')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--screenshots', type=Path)
    run(parser.parse_args().screenshots)
