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
        "msg_id":               "消息ID",
        "query":                "用户消息内容",
        "session_id":           "会话ID",
        "is_group":             true/false,
        "from_user_id":         "发送人ID",
        "from_user_nickname":   "发送人昵称",
        "other_user_id":        "群ID 或 私聊对方ID",
        "other_user_nickname":  "群名 或 私聊对方昵称"
    }

    外部服务返回 JSON：
    {
        "reply": "回复内容"
    }
    或直接返回字符串。
    """

    def reply(self, query, context: Context = None) -> Reply:
        if context.type not in (ContextType.TEXT, ContextType.IMAGE_CREATE):
            return Reply(ReplyType.ERROR, "暂不支持该消息类型")

        webhook_url = conf().get("msg_webhook_url", "")
        if not webhook_url:
            return Reply(ReplyType.ERROR, "[WebhookBot] msg_webhook_url 未配置")

        msg = context.get("msg")
        payload = {
            "msg_id": msg.msg_id if msg else "",
            "query": query,
            "session_id": context.get("session_id", ""),
            "is_group": msg.is_group if msg else False,
            "sender_id": msg.actual_user_id if msg else "",
            "sender_name": msg.actual_user_nickname if msg else "",
        }
        if msg and msg.is_group and msg.other_user_id:
            payload["group_id"] = msg.other_user_id
            payload["group_name"] = msg.other_user_nickname or ""

        timeout = conf().get("msg_webhook_timeout", 10)
        logger.info(f"[Webhook] POST {webhook_url} payload={payload}")

        try:
            resp = requests.post(webhook_url, json=payload, timeout=timeout)
            resp.raise_for_status()

            data = resp.json()
            if isinstance(data, dict):
                reply_text = data.get("reply") or data.get("content") or data.get("message") or str(data)
            else:
                reply_text = str(data)

            logger.info(f"[Webhook] 回复内容: {reply_text}")
            return Reply(ReplyType.TEXT, reply_text)

        except requests.Timeout:
            logger.error(f"[Webhook] 请求超时 ({timeout}s): {webhook_url}")
            return Reply(ReplyType.ERROR, "服务响应超时")
        except requests.HTTPError as e:
            logger.error(f"[Webhook] HTTP错误: {e}")
            return Reply(ReplyType.ERROR, f"服务返回错误: {e}")
        except Exception as e:
            logger.error(f"[Webhook] 调用失败: {e}")
            return Reply(ReplyType.ERROR, f"调用失败: {e}")
