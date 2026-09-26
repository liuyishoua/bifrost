"""Transport for the official component, bound to a pending QR login only."""
import logging

from dy_apis.login_api import DYLoginApi


# Exact paths used by the official component's SMS, password and phone-face flows.
VERIFICATION_METHODS = {
    '/passport/safe/get_auth_ticket/v1/': 'POST',
    '/passport/safe/verify_auth_ticket/': 'POST',
    '/passport/safe/query_decision/': 'GET',
    '/passport/web/get_qrcode/': 'POST',
    '/passport/web/send_code/': 'POST',
    '/passport/web/validate_code/': 'POST',
    '/passport/web/mobile/check_code/': 'POST',
    '/passport/web/account/verify/': 'POST',
    '/passport/upsms/verify/': 'POST',
    '/passport/upsms/safe_mobile/verify/': 'POST',
    '/passport/upsms/chain_mobile/verify/': 'POST',
}


def omit_verification_access_log(record):
    # The SDK puts challenge material in both URL parameters and form bodies.
    return '/verification-request/' not in record.getMessage()


logging.getLogger('werkzeug').addFilter(omit_verification_access_log)


def request_verification(auth, path, method, query, data, incoming_headers):
    if VERIFICATION_METHODS.get(path) != method:
        raise ValueError('不支持该验证接口或请求方法')
    if len(data) > 65536 or sum(len(k) + len(v) for k, v in query) > 32768:
        raise ValueError('验证请求过大')
    try:
        headers = DYLoginApi._passport_headers(auth, form=True, api=path).get()
        for name in ('Content-Type', 'X-Tt-Passport-Trace-Id', 'X-Tt-Passport-Verify-Portrait'):
            if incoming_headers.get(name):
                headers[name.lower()] = incoming_headers[name]
        # No browser Cookies or caller-selected host. Set-Cookie stays in the QR Auth.
        response = auth.request(method, 'https://login.douyin.com' + path,
                                headers=headers, params=query, data=data,
                                allow_redirects=False, verify=True, timeout=20)
        if not 200 <= response.status_code < 300:
            raise RuntimeError('verification upstream rejected request')
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError('verification upstream response format')
        return result
    except Exception:
        raise RuntimeError('verification upstream request failed') from None
