"""Server-side client for the independent Ops Hub identity service."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

COOKIE = "ops_workspace_session"


class IdentityUnavailable(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class IdentityClient:
    def __init__(self, endpoint):
        # Authentication traffic is strictly local; never forward tokens to a proxy.
        if endpoint != "http://127.0.0.1:8787":
            raise ValueError("OPS_IDENTITY_URL must be the fixed local identity service")
        self.endpoint = endpoint
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(self, path, token, data=None):
        req = Request(self.endpoint + "/api/auth/workspace/" + path,
                      data=json.dumps(data).encode() if data is not None else None,
                      headers={"Authorization": "Bearer " + token,
                               "Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=3) as response:
                result = json.load(response)
                cookies = [value for value in response.headers.get_all("Set-Cookie", [])
                           if value.startswith(COOKIE + "=")]
                return result, cookies
        except HTTPError as exc:
            if exc.code in (400, 401, 403, 422):
                return None, []
            raise IdentityUnavailable() from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise IdentityUnavailable() from None

    def verify(self, token):
        if not token:
            return None
        result, _ = self.call("verify", token)
        if result is not None and (not isinstance(result, dict)
                                   or type(result.get("id")) is not int or result["id"] < 1
                                   or not isinstance(result.get("username"), str)
                                   or not result["username"]):
            raise IdentityUnavailable()
        return result


    def access(self, path, token, data=None, method='GET'):
        req = Request(self.endpoint + '/api/auth/workspace/access/' + path,
                      data=json.dumps(data).encode() if data is not None else None,
                      method=method, headers={'Authorization': 'Bearer ' + token,
                      'Content-Type': 'application/json', 'Origin': 'https://d.58nft.net', 'X-App-Request': '1'})
        try:
            with self.opener.open(req, timeout=5) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code in (400, 401, 403, 404, 409, 422):
                try:
                    detail = json.load(exc).get('detail')
                except (ValueError, OSError):
                    detail = None
                raise IdentityRejected(exc.code, detail if isinstance(detail, str) else '提交内容无效，请检查后重试。') from None
            raise IdentityUnavailable() from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise IdentityUnavailable() from None

class IdentityRejected(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message
        super().__init__(message)

