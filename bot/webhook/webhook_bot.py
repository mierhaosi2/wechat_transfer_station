# encoding:utf-8
import requests
from bot.bot import Bot
from bridge.context import Context, ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from config import conf


class WebhookBot(Bot):
    """
    将消息转发给外部 HTTP 服务，并将其返回内容作为回复。

    外部服务接收 POST JSON：
    {
        "msg_id": "消息ID",
        "query": "用户消息内容",
        "session_id": "会话ID",
        "is_group": true/false,
        "sender_id": "发送人ID",
        "sender_name": "发送人昵称",
        "group_id": "群ID（群聊时）",
        "group_name": "群名（群聊时）"
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
        if context.type not in (ContextType.TEXT, ContextType.IMAGE_CREATE):
            return Reply(ReplyType.ERROR, "暂不支持该消息类型")

        webhook_url = conf().get("msg_webhook_url", "")
        if not webhook_url:
            return Reply(ReplyType.ERROR, "[WebhookBot] msg_webhook_url 未配置")

        msg = context.get("msg")
        payload = {
            "event_type": "message",
            "msg_id": msg.msg_id if msg else "",
            "query": query,
            "session_id": context.get("session_id", ""),
            "is_group": msg.is_group if msg else False,
            "sender_id": msg.actual_user_id if msg else "",
            "sender_name": msg.actual_user_nickname if msg else "",
            "is_internal_user": context.get("is_internal_user", False),
            "is_at": msg.is_at if msg else False,
            "silence_mode": context.get("silence_mode", False),
        }
        if msg and msg.is_group and msg.other_user_id:
            payload["group_id"] = msg.other_user_id
            payload["group_name"] = msg.other_user_nickname or ""
            payload["group_owner_id"] = context.get("group_owner_id", "")
            payload["group_owner_name"] = context.get("group_owner_name", "")

        timeout = conf().get("msg_webhook_timeout") or None
        logger.info(f"[Webhook] POST {webhook_url} payload={payload}")

        try:
            resp = requests.post(webhook_url, json=payload, timeout=timeout)
            resp.raise_for_status()

            data = resp.json()
            reply = self._parse_response(data)
            logger.info(f"[Webhook] 回复: type={reply.type}, content={reply.content}")
            return reply

        except requests.Timeout:
            logger.error(f"[Webhook] 请求超时 ({timeout}s): {webhook_url}")
            return Reply(ReplyType.ERROR, "服务响应超时")
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
