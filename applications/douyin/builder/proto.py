#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time : 2024/6/8 下午6:57
# @Author : crush0
# @Description :
import json
import random

import uuid

from utils.fingerprint import get_profile
import static.Request_pb2 as RequestProto
from builder.header import HeaderBuilder
from utils.dy_util import generate_req_sign, generate_millisecond


class ProtoBuilder:
    # The PC IM client rolled its protobuf envelope independently of the
    # main-site version.  These values are present on every current
    # imapi request (conversation reads as well as message sends).
    SDK_VERSION = "0.1.8"
    BUILD_NUMBER = "0d50935:feat/pc-im-groupB"

    @staticmethod
    def build_normal_request(auth, cmd):
        request = RequestProto.Request()
        request.cmd = cmd
        request.sequence_id = random.randint(10000, 11000)
        request.sdk_version = ProtoBuilder.SDK_VERSION
        # The current PC IM envelope leaves the legacy top-level auth token
        # empty.  Authentication is carried by the ticket-guard HTTP headers
        # and (for sends) the conversation ticket in the nested body.
        request.refer = 3
        request.inbox_type = 0
        request.build_number = ProtoBuilder.BUILD_NUMBER
        request.device_id = '0'
        request.device_platform = 'douyin_pc'
        request.version_code = '360000'
        request.headers['session_aid'] = '6383'
        request.headers['session_did'] = '0'
        request.headers['app_name'] = 'douyin_pc'
        request.headers['priority_region'] = 'cn'
        request.headers['user_agent'] = HeaderBuilder.ua
        request.headers['cookie_enabled'] = 'true'
        request.headers['browser_language'] = 'zh-CN'
        request.headers['browser_platform'] = get_profile()["platform"]
        # IM's protobuf client uses navigator.appName/appVersion here, unlike
        # the public REST query helpers which use the Chrome brand/version.
        # Current Chrome wire capture: browser_name="Mozilla" and
        # browser_version is UA without the leading "Mozilla/".
        request.headers['browser_name'] = 'Mozilla'
        request.headers['browser_version'] = get_profile()["ua"].replace(
            'Mozilla/', '', 1
        )
        request.headers['browser_online'] = 'true'
        request.headers['screen_width'] = get_profile()["screen_width"]
        request.headers['screen_height'] = get_profile()["screen_height"]
        # The PC IM shell is entered from the精选 page; the browser puts this
        # page referrer in the protobuf map (it is not the HTTP Referer).
        request.headers['referer'] = 'https://www.douyin.com/jingxuan'
        request.headers['timezone_name'] = 'Asia/Shanghai'
        request.headers['deviceId'] = '0'
        request.headers['is-retry'] = '0'
        request.auth_type = 4
        request.biz = 'douyin_web'
        request.access = 'web_sdk'
        # Current PC IM envelopes authenticate the HTTP request with
        # bd-ticket-guard headers.  Unlike the legacy web-protect envelope,
        # they leave the top-level ts_sign/sdk_cert fields unset; keeping them
        # here makes the body larger and no longer matches Chrome.
        return request

    @staticmethod
    def build_create_conversation_request(auth, toId, myId):
        request = ProtoBuilder.build_normal_request(auth, 609)
        request.body.create_conversation_v2_body.conversation_type = 1
        request.body.create_conversation_v2_body.participants.extend([int(toId), int(myId)])
        reuqest_sign = generate_req_sign({
            "sign_data": f"avatar_url=&idempotent_id=&name=&participants={toId},{myId}",
            "certType": "cookie",
            "scene": "web_protect"
        }, auth.private_key)
        request.reuqest_sign = reuqest_sign
        return request

    @staticmethod
    def build_get_conversation_list_info_request(auth, toId, myId, conversation_short_id):
        request = ProtoBuilder.build_normal_request(auth, 610)
        request.body.get_conversation_info_list_v2_body.data.conversation_id = f"0:1:{myId}:{toId}"
        request.body.get_conversation_info_list_v2_body.data.conversation_short_id = conversation_short_id
        request.body.get_conversation_info_list_v2_body.data.conversation_type = 1
        return request

    @staticmethod
    def build_send_message_request(auth, conversation_id, conversation_short_id, ticket,
                                   message=None, identity_security_token="",
                                   identity_security_device_id="", *,
                                   message_type=7, content=None, ext=None,
                                   mentioned_users=None, client_message_id=None):
        """Build a PC-IM ``message/send`` envelope.

        ``message`` is kept as the first positional argument for backwards
        compatibility with the original text-only helper.  Rich messages pass
        their JSON object through ``content`` and select the wire
        ``message_type`` explicitly.  The protobuf field itself is always a
        compact JSON string, matching the browser SDK.
        """
        if content is None:
            content = message
        if content is None:
            content = ""
        client_message_id = str(client_message_id or uuid.uuid4())
        request = ProtoBuilder.build_normal_request(auth, 100)
        if isinstance(content, (dict, list, tuple)):
            encoded_content = json.dumps(
                content, ensure_ascii=False, separators=(',', ':')
            )
        else:
            encoded_content = str(content)
        request.body.send_message_body.conversation_id = conversation_id
        request.body.send_message_body.conversation_type = 1
        request.body.send_message_body.conversation_short_id = conversation_short_id
        request.body.send_message_body.content = encoded_content
        # The web client sends these extensions on every message.  Preserve
        # caller supplied values while retaining the defaults used by text.
        ext_map = dict(ext or {})
        # Keep the browser's stable default order first; custom extensions are
        # appended afterwards so adding one cannot perturb existing captures.
        defaults = {
            's:mentioned_users': ext_map.pop('s:mentioned_users', ''),
            's:client_message_id': ext_map.pop('s:client_message_id', client_message_id),
        }
        ext_map.pop('s:stime', None)
        for key, value in {**defaults, **ext_map}.items():
            request.body.send_message_body.ext.append(
                RequestProto.ExtValue(key=str(key), value=str(value))
            )
        request.body.send_message_body.ext.append(
            # PC IM uses ``<epoch-ms>.<five random digits>`` for this
            # extension (e.g. ``1788015776891.21440``), not a bare millisecond
            # integer as the legacy client did.
            RequestProto.ExtValue(
                key='s:stime',
                value=f'{generate_millisecond()}.{random.randrange(100000):05d}',
            )
        )
        if mentioned_users:
            request.body.send_message_body.mentioned_users.extend(
                int(uid) for uid in mentioned_users
            )
        request.body.send_message_body.message_type = int(message_type)
        request.body.send_message_body.ticket = ticket
        request.body.send_message_body.client_message_id = client_message_id
        # The current send envelope has no top-level ``reuqest_sign``.  The
        # request is authenticated by bd-ticket-guard HTTP headers; the
        # nested conversation ticket remains part of the protobuf body.
        # Since the 2026 PC IM rollout, message/send carries the short-lived
        # identity-security material in the protobuf header map.  Keep these
        # optional so conversation read/create requests remain usable when a
        # caller only needs the legacy envelope.
        if identity_security_token:
            # The web client wraps the token in a compact JSON object rather
            # than putting the bare token string on the wire.
            request.headers['identity_security_token'] = json.dumps(
                {'token': str(identity_security_token)}, separators=(',', ':')
            )
        if identity_security_device_id:
            request.headers['identity_security_device_id'] = str(identity_security_device_id)
        request.headers['identity_security_aid'] = ''
        return request
