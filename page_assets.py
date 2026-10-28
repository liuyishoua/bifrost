"""Versioned public frontend assets; identity and API responses remain uncached."""
import hashlib
import base64
from pathlib import Path
import re
import threading
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

_ATTRIBUTES = re.compile(r'(?P<prefix>\b(?:src|href)\s*=\s*)(?P<quote>[\"\'])(?P<url>[^\"\']+)(?P=quote)', re.I)
_IMPORTS = re.compile(r'(?P<prefix>\bfrom\s*|\bimport\s*(?:\(\s*)?)(?P<quote>[\"\'])(?P<url>\.{1,2}/[^\"\']+)(?P=quote)')
_CSS_URLS = re.compile(r'url\(\s*(?P<quote>[\"\']?)(?P<url>[^)\"\']+)(?P=quote)\s*\)', re.I)
_VERSION = 'asset_version'


def policy_with_hashes(policy, hashes):
    # Kept for callers with their own small inline bootstrap code.
    directives = [part.strip() for part in policy.split(';') if part.strip()]
    for kind, values in hashes.items():
        for index, directive in enumerate(directives):
            if directive.split()[0] == kind:
                directives[index] = directive + ' ' + ' '.join(sorted(set(values)))
                break
    return '; '.join(directives)


class PageAssets:
    def __init__(self, directory, bundles=None):
        self.directory = Path(directory).resolve()
        self.bundles = bundles or {}
        self.lock = threading.RLock()
        self.stamp = None
        self.version = ''
        self.cache = {}
        self.refresh()

    def refresh(self):
        """A changed dependency versions the whole module graph, including cycles."""
        with self.lock:
            files = sorted(p for p in self.directory.rglob('*') if p.is_file() and not p.is_symlink())
            stamp = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
            if stamp != self.stamp:
                digest = hashlib.sha256()
                for path in files:
                    digest.update(path.relative_to(self.directory).as_posix().encode())
                    digest.update(b'\0')
                    digest.update(hashlib.sha256(path.read_bytes()).digest())
                self.version = digest.hexdigest()[:20]
                self.stamp = stamp
                self.cache.clear()
            return self.version

    def asset(self, url):
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc:
            return None
        path = parsed.path
        if path.startswith('/static/'):
            path = path[8:]
        elif path.startswith('static/'):
            path = path[7:]
        else:
            return None
        target = (self.directory / path).resolve()
        if not target.is_relative_to(self.directory) or not target.is_file():
            return None
        return target

    def asset_url(self, url):
        source = self.asset(url)
        if source is None or source.suffix.lower() == '.html':
            return url
        parsed = urlsplit(url)
        relative = source.relative_to(self.directory).as_posix()
        relative = self.bundles.get(relative, relative)
        query = [(key, value) for key, value in parse_qsl(parsed.query) if key != _VERSION]
        query.append((_VERSION, self.version))
        return urlunsplit(('', '', '/static/' + relative, urlencode(query), parsed.fragment))

    def render(self, filename):
        with self.lock:
            self.refresh()
            key = ('html', filename)
            if key not in self.cache:
                html = (self.directory / filename).read_text(encoding='utf-8')
                html = _ATTRIBUTES.sub(lambda m: m['prefix'] + m['quote'] + self.asset_url(m['url']) + m['quote'], html)
                html = html.replace('<div id="app"></div>', '<div id="app"><div class="workspace-loading" role="status">正在读取工作台…</div></div>', 1)
                hashes = {}
                def theme(match):
                    attrs = match.group(1)
                    src = re.search(r'src=[\"\']([^\"\']+)', attrs)
                    source = self.asset(src[1]) if src else None
                    if source is None or source.name not in ('theme.js', 'workspace-theme.js') or source.stat().st_size > 2048 or 'module' in attrs:
                        return match[0]
                    code = source.read_text(encoding='utf-8')
                    digest = base64.b64encode(hashlib.sha256(code.encode()).digest()).decode()
                    hashes.setdefault('script-src', []).append("'sha256-" + digest + "'")
                    return '<script>' + code + '</script>'
                html = re.sub(r'<script\b([^>]*\bsrc=[^>]*)>\s*</script>', theme, html, flags=re.I)
                preloads = []
                for attrs, src in re.findall(r'<script\b([^>]*?)\bsrc=[\"\']([^\"\']+)[\"\'][^>]*>\s*</script>', html, flags=re.I):
                    if 'module' in attrs and self.asset(src) is not None:
                        preloads.append('<link rel="modulepreload" href="' + src + '">')
                html = html.replace('</head>', ''.join(preloads) + '</head>', 1)
                self.cache[key] = (html, hashes)
            return self.cache[key]

    def content(self, filename):
        with self.lock:
            key = ('asset', filename)
            if key in self.cache:
                return self.cache[key]
            path = self.asset('/static/' + filename)
            if path is None:
                return None
            if path.suffix.lower() in ('.js', '.mjs', '.css'):
                text = path.read_text(encoding='utf-8')
                base = '/static/' + filename
                if path.suffix.lower() in ('.js', '.mjs'):
                    text = _IMPORTS.sub(lambda m: m['prefix'] + m['quote'] + self.asset_url(urljoin(base, m['url'])) + m['quote'], text)
                else:
                    text = _CSS_URLS.sub(lambda m: m[0] if m['url'].strip().startswith(('#', 'data:', 'http:', 'https:', '//')) else 'url(' + m['quote'] + self.asset_url(urljoin(base, m['url'])) + m['quote'] + ')', text)
                data = text.encode('utf-8')
            else:
                data = path.read_bytes()
            self.cache[key] = data
            return data


def install_pages(app, pages, bundles=None):
    from flask import make_response, request
    import mimetypes
    renderer = PageAssets(app.static_folder, bundles)
    original = app.send_static_file

    def send(filename):
        if filename in pages:
            html, hashes = renderer.render(filename)
            response = make_response(html)
            response._workspace_inline = hashes
            response._workspace_entry = True
            return response
        # HTML helpers may have their own strict CSP; never cache verification frames.
        if Path(filename).suffix.lower() == '.html':
            return original(filename)
        data = renderer.content(filename)
        if data is None:
            return original(filename)
        mimetype = 'text/javascript' if Path(filename).suffix.lower() in ('.js', '.mjs') else mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        response = make_response(data)
        response.headers['Content-Type'] = mimetype + ('; charset=utf-8' if mimetype.startswith('text/') else '')
        response.set_etag(hashlib.sha256(data).hexdigest())
        response._workspace_asset_version = renderer.version
        response.make_conditional(request)
        return response

    app.send_static_file = send

    # Flask runs these in reverse order. Override blanket no-store only for public assets.
    @app.after_request
    def asset_headers(response):
        hashes = getattr(response, '_workspace_inline', None)
        if hashes:
            policy = response.headers.get('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'")
            response.headers['Content-Security-Policy'] = policy_with_hashes(policy, hashes)
        version = getattr(response, '_workspace_asset_version', None)
        if version and response.status_code in (200, 304):
            current = request.args.get(_VERSION) == version
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable' if current else 'public, no-cache'
        elif getattr(response, '_workspace_entry', False):
            response.headers['Cache-Control'] = 'no-store'
            response.headers.pop('ETag', None)
        return response
    return renderer
