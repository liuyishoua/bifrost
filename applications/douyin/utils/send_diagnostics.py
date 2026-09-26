"""Allowlisted send diagnostics: never retain response strings or credentials."""
import json
from google.protobuf.empty_pb2 import Empty
from google.protobuf.unknown_fields import UnknownFieldSet
from google.protobuf.message import DecodeError


# Only meanings supported by existing evidence belong here. Unknown codes keep
# their numeric value rather than borrowing meanings from unrelated APIs.
BUSINESS_ERROR_REASONS = {7180: '给陌生人发送消息过于频繁'}
# Product policy confirmed by the user from observations in the Douyin app.
# Preserve these nonzero codes; this is not an official delivery guarantee.
BUSINESS_SUCCESS_CODES = frozenset({4002, 8101, 21003, 31003})

# Local historical observations, not official definitions. Keep uncertainty in
# the displayed text; these annotations never determine success or failure.
BUSINESS_CODE_NOTES = {
    0: '平台返回业务码 0',
    4002: '推测：与接收方类型或账号发送阶段有关；按约定计成功',
    8101: '推测：与陌生人消息或接收方权限分支有关；按约定计成功',
    7180: '给陌生人发送消息过于频繁',
    7173: '推测：接收方或双方关系限制',
    7911: '推测：账号或发送行为风控，不能认定为固定时段限流',
    7278: '原因未确认，样本不足',
    21003: '按约定计成功；原因未确认，当前研究样本均为非蓝 V 接收方',
    31003: '按约定计成功；原因未确认，当前研究样本均为非蓝 V 接收方',
    10402: '原因未确认，样本不足',
}


def accepted_without_business_code(diagnostic):
    message_id = diagnostic.get('server_message_id')
    positive_id = ((type(message_id) is int and message_id > 0) or
                   (isinstance(message_id, str) and message_id.isascii()
                    and message_id.isdigit() and bool(message_id.strip('0'))))
    return (diagnostic.get('reason') in (None, 'incomplete_response', 'accepted_without_business_code')
            and diagnostic.get('http_status') == 200
            and diagnostic.get('message_ok') is True
            and diagnostic.get('has_error_desc') is False
            and diagnostic.get('wire_codes_unreadable') is False
            and diagnostic.get('business_result_state') == 'missing'
            and diagnostic.get('business_code') is None
            and diagnostic.get('missing_success_fields') == ['business_code']
            and all(type(diagnostic.get(k)) is int and diagnostic[k] == 0
                    for k in ('outer_status', 'send_status', 'check_code'))
            and positive_id)


def send_result_note(diagnostic):
    if accepted_without_business_code(diagnostic):
        return '未返回业务码'
    code = diagnostic.get('business_code')
    return BUSINESS_CODE_NOTES.get(code, '原因未确认') if type(code) is int else ''


def send_failure_reason(diagnostic):
    code = diagnostic.get('business_code')
    if type(code) is int and code != 0:
        meaning = BUSINESS_ERROR_REASONS.get(code)
        return f'{meaning}（{code}）' if meaning else f'平台返回错误码 {code}'
    reason = diagnostic.get('reason')
    if reason == 'account_issue':
        stage = {'create_conversation': '创建会话', 'identity_token': '获取临时身份令牌'}.get(diagnostic.get('stage'), '会话或凭证检查')
        cause = {'network_timeout': '网络请求超时', 'network_error': '网络请求异常'}.get(diagnostic.get('cause_reason'))
        return f'{stage}时{cause}，本条未提交发送' if cause else f'{stage}失败，请检查账号发送状态'
    if reason == 'image_upload_failed':
        return '图片上传失败，本条消息未提交；不自动重发'
    if reason == 'rate_limited':
        return f"{BUSINESS_ERROR_REASONS[7180]}（7180）"
    if reason == 'platform_rejected':
        return '平台拒绝发送'
    detail = {'missing': '未返回业务结果字段', 'empty': '业务结果字段为空',
              'invalid_json': '业务结果无法解析为 JSON', 'non_object': '业务结果不是 JSON 对象',
              'missing_status_code': '业务结果缺少 status_code',
              'invalid_status_code_type': '业务码不是整数',
              'invalid_wire_type': '业务结果字段类型不符'}.get(diagnostic.get('business_result_state'))
    if not detail:
        detail = {'unknown_fields': '响应包含未知字段，缺少成功凭据',
                  'protobuf_decode_error': '响应协议解析失败',
                  'network_timeout': '网络请求超时，未收到成功凭据',
                  'network_error': '网络请求异常，未收到成功凭据',
                  'interrupted': '执行中断，未取得成功结果',
                  'internal_error': '发送处理发生内部异常'}.get(reason, '响应缺少完整成功凭据')
    return detail + '；不自动重发'


class SendUncertain(RuntimeError):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__('发送结果未确认')


class ImageUploadError(RuntimeError):
    def __init__(self, cause):
        self.diagnostic = {**cause, 'reason': 'image_upload_failed',
                           'stage': 'image_upload', 'cause_reason': cause['reason']}
        super().__init__('图片上传失败，本条消息未提交')


class ConversationError(RuntimeError):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__('创建会话未完成，详见脱敏诊断')


def exception_diagnostic(exc):
    from curl_cffi.requests.exceptions import RequestException, Timeout
    from google.protobuf.message import DecodeError
    reason = 'internal_error'
    if isinstance(exc, (TimeoutError, Timeout)):
        reason = 'network_timeout'
    elif isinstance(exc, (ConnectionError, RequestException)):
        reason = 'network_error'
    elif isinstance(exc, DecodeError):
        reason = 'protobuf_decode_error'
    result = {'reason': reason, 'exception_type': type(exc).__name__}
    if isinstance(exc, KeyError) and exc.args and exc.args[0] in (
            'body', 'create_conversation_v2_body', 'conversation_info_list',
            'conversation_id', 'conversation_short_id', 'ticket', 'user_uid'):
        result['missing_field'] = exc.args[0]
    return result


def conversation_response_diagnostic(resp):
    raw = resp.content
    stripped = raw.lstrip()
    kind = ('empty' if not stripped else 'json' if stripped[:1] in (b'{', b'[')
            else 'html' if stripped.startswith(b'<') else 'protobuf')
    result = {'http_status': resp.status_code, 'response_bytes': len(raw), 'response_format': kind,
              'has_verification_challenge': bool(resp.headers.get('X-Tt-Verify-Passport-Decision')
                                                or resp.headers.get('X-Vc-Bdturing-Parameters'))}
    if kind == 'json':
        try:
            value = json.loads(raw)
            data = value.get('data') if isinstance(value, dict) else None
            for node in (data, value):
                if isinstance(node, dict):
                    for key in ('error_code', 'status_code'):
                        if type(node.get(key)) is int:
                            result['response_code'] = node[key]
                            return result
        except (ValueError, UnicodeError):
            pass
    elif kind == 'protobuf':
        fields = []
        def visit(data, prefix=''):
            for field in UnknownFieldSet(Empty.FromString(data)):
                if len(fields) >= 80:
                    result['wire_fields_truncated'] = True
                    return
                path = prefix + str(field.field_number)
                fields.append({'path': path, 'wire_type': field.wire_type})
                if path == '3' and field.wire_type == 0:
                    result['outer_status'] = field.data
                elif path in ('6', '6.609') and field.wire_type == 2:
                    visit(field.data, path + '.')
        try:
            visit(raw)
        except DecodeError:
            result['wire_codes_unreadable'] = True
        result['wire_fields'] = fields
    return result


def response_diagnostic(response, *, http_status=None):
    unknown = []
    def visit(message, prefix=''):
        for field in UnknownFieldSet(message):
            unknown.append({'path': prefix + str(field.field_number), 'wire_type': field.wire_type})
        for descriptor, value in message.ListFields():
            if descriptor.message_type:
                children = value if descriptor.is_repeated else [value]
                for child in children:
                    visit(child, prefix + str(descriptor.number) + '.')
    visit(response)
    # Decode the send envelope independently: legacy Response.proto has the
    # wrong wire type for field 3 and omits ResponseBody.send_message_body=100.
    # Field names match the IM SDK; field numbers are backed by local captures.
    codes = {}
    send = {'business_result_state': 'missing'}
    def wire_codes(data, prefix=''):
        for field in UnknownFieldSet(Empty.FromString(data)):
            path = prefix + str(field.field_number)
            if field.wire_type == 0 and path in ('3', '6.100.3', '6.100.5'):
                codes[path] = field.data
            elif field.wire_type == 0 and path == '6.100.1':
                send['server_message_id'] = str(field.data)
            elif path == '6.100.6':
                send['business_result_wire_type'] = field.wire_type
                if field.wire_type != 2:
                    send['business_result_state'] = 'invalid_wire_type'
                    continue
                send['business_result_bytes'] = len(field.data)
                if not field.data:
                    send['business_result_state'] = 'empty'
                    continue
                try:
                    detail = json.loads(field.data)
                    if not isinstance(detail, dict):
                        send['business_result_state'] = 'non_object'
                    elif 'status_code' not in detail:
                        send['business_result_state'] = 'missing_status_code'
                    elif type(detail['status_code']) is not int:
                        send['business_result_state'] = 'invalid_status_code_type'
                        send['business_code_type'] = type(detail['status_code']).__name__
                    else:
                        send['business_result_state'] = 'parsed'
                        send['business_code'] = detail['status_code']
                except (ValueError, UnicodeError):
                    send['business_result_state'] = 'invalid_json'
            elif field.wire_type == 2 and path in ('6', '6.100'):
                wire_codes(field.data, path + '.')
    unreadable = False
    try:
        wire_codes(response.SerializeToString())
    except DecodeError:
        unreadable = True
    result = {'message_ok': response.message == 'OK', 'has_error_desc': bool(response.error_desc),
              'unknown_fields': unknown, 'wire_codes': codes, 'wire_codes_unreadable': unreadable,
              **send}
    if http_status is not None:
        result['http_status'] = http_status
    result['outer_status'] = codes.get('3')
    result['send_status'] = codes.get('6.100.3')
    result['check_code'] = codes.get('6.100.5')
    result['missing_success_fields'] = [key for key in ('outer_status', 'send_status', 'check_code', 'business_code')
                                        if result.get(key) is None]
    if int(send.get('server_message_id', '0')) <= 0:
        result['missing_success_fields'].append('server_message_id')
    code = send.get('business_code')
    statuses = [result[k] for k in ('outer_status', 'send_status', 'check_code')]
    if code in BUSINESS_SUCCESS_CODES:
        reason = 'business_accepted'
    elif code == 7180:
        reason = 'rate_limited'
    elif any(s is not None and s != 0 for s in statuses) or not result['message_ok'] or result['has_error_desc']:
        reason = 'platform_rejected'
    elif code is not None and code != 0:
        reason = 'platform_rejected'
    elif not unreadable and statuses == [0, 0, 0] and code == 0 and int(send.get('server_message_id', '0')) > 0:
        reason = 'interface_accepted'
    elif accepted_without_business_code(result):
        reason = 'accepted_without_business_code'
    else:
        reason = 'incomplete_response'
    result['reason'] = reason
    return result
