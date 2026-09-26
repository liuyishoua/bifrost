"""Per-account cookie storage; save after requests has applied Set-Cookie."""
import os
import tempfile
import warnings
from http.cookiejar import LWPCookieJar

from .diagnostics import DiagnosticSession, cookie_summary


class AccountSession(DiagnosticSession):
    def __init__(self, path, observer=None):
        super().__init__(observer)
        self.last_saved = None
        self.path = path
        self.enabled = False

    def restore(self):
        if not self.path.exists():
            self.emit('cookie_missing', '本地没有已保存的会话')
            return False
        jar = LWPCookieJar(str(self.path))
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                jar.load(ignore_discard=True, ignore_expires=True)
        except (OSError, ValueError) as exc:
            self.emit('cookie_restore_failed', '本地会话读取失败', exception_type=type(exc).__name__)
            raise
        expired_discarded = sum(c.is_expired() for c in jar)
        jar.clear_expired_cookies()
        self.cookies.update(jar)
        self.enabled = bool(self.cookies)
        self.emit('cookie_restored', '已读取本地会话，使用前仍需校验', cookies=cookie_summary(self.cookies), expired_discarded=expired_discarded)
        return self.enabled

    def save(self):
        if not self.enabled:
            return
        temporary = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, suffix='.tmp')
            os.close(fd)
            jar = LWPCookieJar(temporary)
            for cookie in self.cookies:
                jar.set_cookie(cookie)
            jar.save(ignore_discard=True, ignore_expires=True)
            os.replace(temporary, self.path)
            signature = jar.as_lwp_str(ignore_discard=True, ignore_expires=True)
            if signature != self.last_saved:
                self.emit('cookie_saved', '会话凭证已保存', preview=False, cookies=cookie_summary(self.cookies))
                self.last_saved = signature
        except OSError as exc:
            self.emit('cookie_save_failed', '会话凭证保存失败，内存凭证保留', exception_type=type(exc).__name__)
            raise
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)

    def forget(self):
        self.enabled = False
        self.cookies.clear()
        self.last_saved = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            self.emit('cookie_delete_failed', '本地会话凭证删除失败', exception_type=type(exc).__name__)
            raise
        self.emit('cookie_deleted', '本地会话凭证已删除')

    def request(self, *args, **kwargs):
        try:
            return super().request(*args, **kwargs)
        finally:
            self.save()
