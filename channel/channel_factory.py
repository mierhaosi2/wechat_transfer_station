"""
channel factory
"""
from .channel import Channel


def create_channel(channel_type) -> Channel:
    """
    create a channel instance
    :param channel_type: channel type code
    :return: channel instance
    """
    ch = Channel()
    if channel_type == "wx":
        from channel.wechat.wechat_channel import WechatChannel
        ch = WechatChannel()
    elif channel_type == "wxy":
        from channel.wechat.wechaty_channel import WechatyChannel
        ch = WechatyChannel()
    elif channel_type == "wechatmp":
        from channel.wechatmp.wechatmp_channel import WechatMPChannel
        ch = WechatMPChannel(passive_reply=True)
    elif channel_type == "wechatmp_service":
        from channel.wechatmp.wechatmp_channel import WechatMPChannel
        ch = WechatMPChannel(passive_reply=False)
    elif channel_type == "wechatcom_app":
        from channel.wechatcom.wechatcomapp_channel import WechatComAppChannel
        ch = WechatComAppChannel()
    elif channel_type == "wechatcom_service":
        from channel.wechatcs.wechatcomservice_channel import WechatComServiceChannel
        ch = WechatComServiceChannel()
    elif channel_type == "wework":
        from channel.wework.wework_channel import WeworkChannel
        ch = WeworkChannel()
    else:
        raise RuntimeError(f"Unsupported channel type: {channel_type}")
    ch.channel_type = channel_type
    return ch
