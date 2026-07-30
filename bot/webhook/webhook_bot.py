# encoding:utf-8
import base64
import os
import re as _re

import requests
from bot.bot import Bot
from bridge.context import Context, ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from config import conf


def _is_http_url(text: str) -> bool:
    """整段内容是否为 http(s) 链接（允许末尾少量空白/标点）。"""
    if not text:
        return False
    s = text.strip().rstrip(")。.,，。!！?？\"'」』】>")
    return bool(_re.match(r"^https?://\S+$", s, _re.IGNORECASE))


class WebhookBot(Bot):
    """
    将消息转发给外部 HTTP 服务，并将其返回内容作为回复。

    外部服务接收 POST JSON（文本）：
    {
        "event_type": "message",
        "msg_type": "text",
        "msg_id": "消息ID",
        "query": "用户消息内容",
        "session_id": "会话ID",
        "is_group": true/false,
        "sender_id": "发送人ID",
        "sender_name": "发送人昵称",
        "group_id": "群ID（群聊时）",
        "group_name": "群名（群聊时）"
    }

    图片消息额外字段：
    {
        "msg_type": "image",
        "query": "",
        "image_base64": "...",
        "image_name": "xxx.jpg"
    }

    链接/分享消息：
    {
        "msg_type": "link",
        "query": "https://...",
        "url": "https://...",
        "title": "...",
        "desc": "...",
        "image_url": "..."
    }

    语音消息：
    {
        "msg_type": "voice",
        "query": "",
        "voice_base64": "...",
        "voice_name": "xxx.wav",
        "duration": 2
    }

    外部服务返回 JSON 示例：

    1) 纯文本（默认）
    {"reply": "你好"}

    2) 图片
    {"reply_type": "image", "image_url": "https://example.com/a.png"}

    3) 链接/图文卡片
    {
        "reply_type": "link_card",
        "title": "标题",
        "desc": "描述",
        "url": "https://example.com",
        "image_url": "https://example.com/cover.png"
    }
    或使用 link_card 对象：
    {
        "reply_type": "link_card",
        "link_card": {"title": "...", "desc": "...", "url": "...", "image_url": "..."}
    }

    4) 文本 + 多张图片（先发文字，再按顺序发图）
    {
        "reply_type": "text",
        "reply": "说明文字",
        "image_urls": ["https://example.com/1.png", "https://example.com/2.png"]
    }
    """

    def reply(self, query, context: Context = None) -> Reply:
        if context.type not in (
            ContextType.TEXT,
            ContextType.IMAGE_CREATE,
            ContextType.IMAGE,
            ContextType.SHARING,
            ContextType.VOICE,
        ):
            return Reply(ReplyType.ERROR, "暂不支持该消息类型")

        webhook_url = conf().get("msg_webhook_url", "")
        if not webhook_url:
            return Reply(ReplyType.ERROR, "[WebhookBot] msg_webhook_url 未配置")

        msg = context.get("msg")
        is_at = msg.is_at if msg else False

        msg_type = "text"
        image_base64 = ""
        image_name = ""
        voice_base64 = ""
        voice_name = ""
        voice_duration = 0
        clean_query = ""
        link_url = ""
        link_title = ""
        link_desc = ""
        link_image_url = ""

        if context.type == ContextType.IMAGE:
            msg_type = "image"
            image_path = context.content or ""
            image_name = os.path.basename(image_path) if image_path else ""
            if not image_path or not os.path.isfile(image_path):
                return Reply(ReplyType.ERROR, f"图片文件不存在: {image_path}")
            try:
                with open(image_path, "rb") as f:
                    image_base64 = base64.b64encode(f.read()).decode("ascii")
            except Exception as e:
                return Reply(ReplyType.ERROR, f"读取图片失败: {e}")
        elif context.type == ContextType.VOICE:
            msg_type = "voice"
            voice_path = context.content or ""
            voice_name = os.path.basename(voice_path) if voice_path else ""
            voice_duration = getattr(msg, "voice_duration", None) or 0
            if not voice_path or not os.path.isfile(voice_path):
                return Reply(ReplyType.ERROR, f"语音文件不存在: {voice_path}")
            try:
                with open(voice_path, "rb") as f:
                    voice_base64 = base64.b64encode(f.read()).decode("ascii")
            except Exception as e:
                return Reply(ReplyType.ERROR, f"读取语音失败: {e}")
        elif context.type == ContextType.SHARING:
            msg_type = "link"
            link_url = (context.content or "").strip()
            clean_query = link_url
            link_title = getattr(msg, "share_title", None) or ""
            link_desc = getattr(msg, "share_desc", None) or ""
            link_image_url = getattr(msg, "share_image_url", None) or ""
            if not link_url and msg and getattr(msg, "_rawmsg", None):
                data = (msg._rawmsg or {}).get("data") or {}
                link_url = data.get("url") or data.get("link_url") or ""
                clean_query = link_url
                link_title = link_title or data.get("title") or data.get("name") or ""
                link_desc = link_desc or data.get("desc") or data.get("description") or ""
                link_image_url = link_image_url or data.get("image_url") or data.get("img_url") or ""
            if not link_url:
                return Reply(ReplyType.ERROR, "链接消息缺少 url")
        else:
            # @ 机器人时去掉开头的 @名字，只取实际问题内容
            clean_query = _re.sub(r'@\S+[\u2005\u0020]*', '', query or '').strip() if is_at else (query or '')
            # 纯文本里直接贴的 http(s) 链接（公众号/小红书等）也按 link 回调
            if _is_http_url(clean_query):
                msg_type = "link"
                link_url = clean_query

        payload = {
            "event_type": "message",
            "msg_type": msg_type,
            "msg_id": msg.msg_id if msg else "",
            "query": clean_query,
            "session_id": context.get("session_id", ""),
            "is_group": msg.is_group if msg else False,
            "sender_id": msg.actual_user_id if msg else "",
            "sender_name": msg.actual_user_nickname if msg else "",
            "is_internal_user": context.get("is_internal_user", False),
            "is_at": msg.is_at if msg else False,
            "is_group_owner": bool(context.get("is_group_owner", False)),
            "silence_mode": context.get("silence_mode", False),
            "silence_reason": context.get("silence_reason", ""),
            "corp_id": conf().get("wechatcom_corp_id", ""),
        }
        if msg_type == "image":
            payload["image_base64"] = image_base64
            payload["image_name"] = image_name
        elif msg_type == "voice":
            payload["voice_base64"] = voice_base64
            payload["voice_name"] = voice_name
            payload["duration"] = voice_duration
        elif msg_type == "link":
            payload["url"] = link_url
            payload["title"] = link_title
            payload["desc"] = link_desc
            payload["image_url"] = link_image_url
        if msg and msg.is_group and msg.other_user_id:
            payload["group_id"] = msg.other_user_id
            payload["group_name"] = msg.other_user_nickname or ""
            payload["group_owner_id"] = context.get("group_owner_id", "")
            payload["group_owner_name"] = context.get("group_owner_name", "")

        timeout = conf().get("msg_webhook_timeout") or None
        log_payload = dict(payload)
        if "image_base64" in log_payload:
            b64 = log_payload["image_base64"]
            log_payload["image_base64"] = f"<{len(b64)} chars>"
        if "voice_base64" in log_payload:
            b64 = log_payload["voice_base64"]
            log_payload["voice_base64"] = f"<{len(b64)} chars>"
        logger.info(f"[Webhook] POST {webhook_url} payload={log_payload}")

        try:
            resp = requests.post(webhook_url, json=payload, timeout=timeout)
            resp.raise_for_status()

            data = resp.json()
            logger.info(f"[Webhook] 原始响应: {data}")
            reply = self._parse_response(data)
            reply.at_manager = bool(data.get("at_manager", False))
            reply.answer = data.get("answer", "")
            logger.info(f"[Webhook] 回复: type={reply.type}, content={reply.content}, at_manager={reply.at_manager}, answer={reply.answer}")
            return reply

        except requests.Timeout:
            logger.error(f"[Webhook] 请求超时 ({timeout}s): {webhook_url}")
            return Reply(ReplyType.ERROR, "服务响应超时")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[Webhook] 连接失败（服务不可达，静默忽略）: {e}")
            return None
        except requests.HTTPError as e:
            logger.error(f"[Webhook] HTTP错误: {e}")
            return Reply(ReplyType.ERROR, f"服务返回错误: {e}")
        except ValueError as e:
            logger.error(f"[Webhook] 响应解析失败: {e}")
            return Reply(ReplyType.ERROR, str(e))
        except Exception as e:
            logger.error(f"[Webhook] 调用失败: {e}")
            return Reply(ReplyType.ERROR, f"调用失败: {e}")

    def _parse_response(self, data) -> Reply:
        if not isinstance(data, dict):
            return Reply(ReplyType.TEXT, str(data))

        # 全空响应：服务明确表示无需回复
        reply_type = (data.get("reply_type") or data.get("type") or "").lower().strip()
        has_content = any([
            data.get("reply"), data.get("content"), data.get("message"),
            data.get("image_url"), data.get("image_urls"), data.get("url"),
            data.get("gif_url"), data.get("title"), data.get("desc"),
            data.get("link_card"),
        ])
        if not has_content:
            logger.info("[Webhook] 服务返回全空，跳过回复")
            return Reply(ReplyType.TEXT, None)
        image_urls = self._collect_image_urls(data)

        # 文本 + 多图：先发文字，再发图片
        if image_urls and reply_type in ("", "text", "txt"):
            reply_text = data.get("reply") or data.get("content") or data.get("message")
            return self._build_multi_reply(reply_text, image_urls)

        # 链接/图文卡片
        if reply_type in ("link_card", "link", "card", "sharing"):
            card = data.get("link_card") or data
            url = card.get("url") or data.get("url")
            if not url:
                raise ValueError("link_card 缺少 url 字段")
            return Reply(
                ReplyType.LINK_CARD,
                {
                    "title": card.get("title") or data.get("title") or url,
                    "desc": card.get("desc") or data.get("description") or data.get("desc") or "",
                    "url": url,
                    "image_url": card.get("image_url") or data.get("image_url") or "",
                    "text_desc": card.get("text_desc") or data.get("text_desc") or "",
                    "answer": card.get("answer") or data.get("answer") or "",
                },
            )

        # 仅返回 url 时，按链接卡片发送
        if data.get("url") and not data.get("reply") and not data.get("content") and not data.get("message"):
            return Reply(
                ReplyType.LINK_CARD,
                {
                    "title": data.get("title") or data["url"],
                    "desc": data.get("desc") or data.get("description") or "",
                    "url": data["url"],
                    "image_url": data.get("image_url") or "",
                    "text_desc": data.get("text_desc") or "",
                    "answer": data.get("answer") or "",
                },
            )

        # 图片
        if reply_type in ("image", "image_url"):
            image_url = data.get("image_url") or data.get("url")
            if not image_url:
                raise ValueError("image 类型缺少 image_url 字段")
            return Reply(ReplyType.IMAGE_URL, image_url)

        if data.get("image_url") and not data.get("reply") and not data.get("content"):
            return Reply(ReplyType.IMAGE_URL, data["image_url"])

        # 仅多图、无文字
        if image_urls:
            return self._build_multi_reply(None, image_urls)

        # 默认文本
        reply_text = data.get("reply") or data.get("content") or data.get("message")
        if reply_text is None:
            reply_text = str(data)
        return Reply(ReplyType.TEXT, reply_text)

    @staticmethod
    def _collect_image_urls(data: dict) -> list:
        urls = []
        raw = data.get("image_urls")
        if isinstance(raw, list):
            urls.extend(u for u in raw if u)
        elif isinstance(raw, str) and raw:
            urls.append(raw)
        single = data.get("image_url")
        if single and single not in urls:
            urls.append(single)
        return urls

    def _build_multi_reply(self, text, image_urls: list) -> Reply:
        max_images = conf().get("max_media_send_count", 3)
        replies = []
        if text:
            replies.append(Reply(ReplyType.TEXT, text))
        for url in image_urls[:max_images]:
            replies.append(Reply(ReplyType.IMAGE_URL, url))
        if not replies:
            raise ValueError("image_urls 为空且无文本内容")
        if len(replies) == 1:
            return replies[0]
        if len(image_urls) > max_images:
            logger.warning(f"[Webhook] image_urls 超过 max_media_send_count={max_images}，已截断")
        return Reply(ReplyType.MULTI, replies)
