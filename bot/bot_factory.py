"""
bot factory
"""
from common import const


def create_bot(bot_type):
    if bot_type == const.DIFY:
        from bot.dify.dify_bot import DifyBot
        return DifyBot()

    if bot_type == const.WEBHOOK:
        from bot.webhook.webhook_bot import WebhookBot
        return WebhookBot()

    raise RuntimeError(f"Unsupported bot type: {bot_type}")
