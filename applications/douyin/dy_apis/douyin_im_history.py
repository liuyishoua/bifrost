"""Read one existing conversation page; never send, create, or mark read."""
import json

from google.protobuf.empty_pb2 import Empty
from google.protobuf.unknown_fields import UnknownFieldSet

from builder.header import HeaderBuilder, HeaderType
from builder.proto import ProtoBuilder
from utils import http_client


class HistoryError(RuntimeError):
    def __init__(self, reason, **diagnostic):
        super().__init__('历史消息读取未完成')
        self.diagnostic = {'reason': reason, **diagnostic}


def _integer(value, label, minimum=0, maximum=2**63-1):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
        raise ValueError(f'{label}必须为整数')
    value = int(value)
    if not minimum <= value <= maximum:
        raise ValueError(f'{label}超出范围')
    return value


def _varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result) + bytes([value])


def _number(field, value):
    return _varint(field << 3) + _varint(value)


def _data(field, value):
    return _varint(field << 3 | 2) + _varint(len(value)) + value


def _fields(raw):
    result = {}
    for field in UnknownFieldSet(Empty.FromString(raw)):
        result.setdefault(field.field_number, []).append((field.wire_type, field.data))
    return result


def _one(fields, number, wire, default=None):
    values = fields.get(number, [])
    if not values:
        return default
    if len(values) != 1 or values[0][0] != wire:
        raise ValueError('invalid field')
    return values[0][1]


def get_conversation_messages(auth, conversation_id, conversation_short_id, *, direction='latest', cursor=0, count=50):
    """Return messages, next_cursor and has_more, preserving int64 IDs as strings.

    Wire fields come from Douyin's web SDK and were verified with real pages.
    The caller persists a successful page before advancing its own cursor.
    """
    directions = {'older': 1, 'newer': 2, 'latest': 3}
    if not isinstance(direction, str) or direction not in directions:
        raise ValueError('读取方向无效')
    if not isinstance(conversation_id, str) or not conversation_id or len(conversation_id) > 200:
        raise ValueError('会话 ID 无效')
    short_id = _integer(conversation_short_id, '会话 short ID', 1)
    anchor = _integer(cursor, '消息游标')
    limit = _integer(count, '每页条数', 1, 100)
    payload = (_data(1, conversation_id.encode()) + _number(2, 1) + _number(3, short_id)
               + _number(4, directions[direction]) + _number(5, anchor) + _number(6, limit))
    diagnostic = {}
    try:
        request = ProtoBuilder.build_normal_request(auth, 301)
        # Keep the legacy envelope; only add the SDK's history body at field 301.
        request.body.MergeFromString(_data(301, payload))
        path = '/v1/message/get_by_conversation'
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header('referer', 'https://www.douyin.com/')
        headers.with_bd(path, auth)
        response = http_client.post('https://imapi.douyin.com' + path, headers=headers.get(),
                                    cookies=auth.cookie, data=request.SerializeToString(), verify=True)
        diagnostic['http_status'] = response.status_code
        if response.status_code != 200:
            raise HistoryError('http_error', **diagnostic)
        outer = _fields(response.content)
        diagnostic['outer_status'] = _one(outer, 3, 0, 0)
        if diagnostic['outer_status'] != 0:
            raise HistoryError('platform_rejected', **diagnostic)
        if _one(outer, 1, 0) != 301:
            raise ValueError('unexpected command')
        body = _fields(_one(outer, 6, 2))
        page = _fields(_one(body, 301, 2))
        next_cursor = _one(page, 2, 0)
        more = _one(page, 3, 0, 0)
        if type(next_cursor) is not int or next_cursor > 2**63-1 or more not in (0, 1):
            raise ValueError('invalid pagination')
        messages = []
        for wire, raw in page.get(1, []):
            if wire != 2:
                raise ValueError('invalid message')
            item = _fields(raw)
            if (_one(item, 1, 2) != conversation_id.encode()
                    or _one(item, 5, 0) != short_id):
                raise ValueError('unexpected conversation')
            mid, sender, index = (_one(item, field, 0) for field in (3, 7, 4))
            if any(type(v) is not int or not 0 < v < 2**63 for v in (mid, sender, index)):
                raise ValueError('invalid message identity')
            content = _one(item, 8, 2, b'').decode('utf-8')
            kind, created = _one(item, 6, 0, 0), _one(item, 10, 0)
            text = None
            if kind == 7:
                try:
                    value = json.loads(content)
                    if isinstance(value, dict) and isinstance(value.get('text'), str):
                        text = value['text']
                except ValueError:
                    pass
            messages.append({'conversation_id': conversation_id, 'message_id': str(mid),
                             'sender_uid': str(sender), 'index': str(index), 'message_type': kind,
                             'created_at': created/1000 if created is not None else None,
                             'content': content, 'text': text})
        return {'messages': messages, 'next_cursor': str(next_cursor), 'has_more': bool(more)}
    except HistoryError:
        raise
    except Exception as exc:
        raise HistoryError('history_read_failed', exception_type=type(exc).__name__, **diagnostic) from None
