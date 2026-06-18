import io
import os
import random
import tempfile
import threading
import requests
import ntwork
import uuid
import time

from bridge.context import *
from bridge.reply import *
from channel.chat_channel import ChatChannel
from channel.wework.wework_message import *
from channel.wework.wework_message import WeworkMessage
from common.singleton import singleton
from common.log import logger
from common.time_check import time_checker
from common.utils import compress_imgfile, fsize
from config import conf
from channel.wework.run import wework
from channel.wework import run
from PIL import Image

# 记录每个群最近一次内部用户发言的时间戳 {group_id: timestamp}
_internal_user_last_msg_time: dict = {}

def get_wxid_by_name(room_members, group_wxid, name):
    if group_wxid in room_members:
        for member in room_members[group_wxid]['member_list']:
            if member['room_nickname'] == name or member['username'] == name:
                return member['user_id']
    return None  # 如果没有找到对应的group_wxid或name，则返回None


def download_and_compress_image(url, filename, quality=30):
    # 确定保存图片的目录
    directory = os.path.join(os.getcwd(), "tmp")
    # 如果目录不存在，则创建目录
    if not os.path.exists(directory):
        os.makedirs(directory)

    # 下载图片
    pic_res = requests.get(url, stream=True)
    image_storage = io.BytesIO()
    for block in pic_res.iter_content(1024):
        image_storage.write(block)

    # 检查图片大小并可能进行压缩
    sz = fsize(image_storage)
    if sz >= 10 * 1024 * 1024:  # 如果图片大于 10 MB
        logger.info("[wework] image too large, ready to compress, sz={}".format(sz))
        image_storage = compress_imgfile(image_storage, 10 * 1024 * 1024 - 1)
        logger.info("[wework] image compressed, sz={}".format(fsize(image_storage)))

    # 将内存缓冲区的指针重置到起始位置
    image_storage.seek(0)

    # 读取并保存图片
    image = Image.open(image_storage)
    image_path = os.path.join(directory, f"{filename}.png")
    image.save(image_path, "png")

    return image_path


def download_video(url, filename):
    # 确定保存视频的目录
    directory = os.path.join(os.getcwd(), "tmp")
    # 如果目录不存在，则创建目录
    if not os.path.exists(directory):
        os.makedirs(directory)

    # 下载视频
    response = requests.get(url, stream=True)
    total_size = 0

    video_path = os.path.join(directory, f"{filename}.mp4")

    with open(video_path, 'wb') as f:
        for block in response.iter_content(1024):
            total_size += len(block)

            # 如果视频的总大小超过30MB (30 * 1024 * 1024 bytes)，则停止下载并返回
            if total_size > 30 * 1024 * 1024:
                logger.info("[WX] Video is larger than 30MB, skipping...")
                return None

            f.write(block)

    return video_path


def create_message(wework_instance, message, is_group):
    logger.debug(f"正在为{'群聊' if is_group else '单聊'}创建 WeworkMessage")
    cmsg = WeworkMessage(message, wework=wework_instance, is_group=is_group)
    logger.debug(f"cmsg:{cmsg}")
    return cmsg


def handle_message(cmsg, is_group):
    logger.debug(f"准备用 WeworkChannel 处理{'群聊' if is_group else '单聊'}消息")
    if is_group:
        WeworkChannel().handle_group(cmsg)
    else:
        WeworkChannel().handle_single(cmsg)
    logger.debug(f"已用 WeworkChannel 处理完{'群聊' if is_group else '单聊'}消息")


def _check(func):
    def wrapper(self, cmsg: ChatMessage):
        msgId = cmsg.msg_id
        create_time = cmsg.create_time  # 消息时间戳
        if create_time is None:
            return func(self, cmsg)
        if int(create_time) < int(time.time()) - 60:  # 跳过1分钟前的历史消息
            logger.debug("[WX]history message {} skipped".format(msgId))
            return
        return func(self, cmsg)

    return wrapper


@wework.msg_register(
    [ntwork.MT_RECV_TEXT_MSG, ntwork.MT_RECV_IMAGE_MSG, 11072, ntwork.MT_RECV_LINK_CARD_MSG,ntwork.MT_RECV_FILE_MSG, ntwork.MT_RECV_VOICE_MSG])
def all_msg_handler(wework_instance: ntwork.WeWork, message):
    logger.debug(f"收到消息: {message}")
    if 'data' in message:
        # 首先查找conversation_id，如果没有找到，则查找room_conversation_id
        conversation_id = message['data'].get('conversation_id', message['data'].get('room_conversation_id'))
        if conversation_id is not None:
            is_group = "R:" in conversation_id
            try:
                cmsg = create_message(wework_instance=wework_instance, message=message, is_group=is_group)
            except NotImplementedError as e:
                logger.error(f"[WX]{message.get('MsgId', 'unknown')} 跳过: {e}")
                return None
            delay = random.randint(1, 2)
            timer = threading.Timer(delay, handle_message, args=(cmsg, is_group))
            timer.start()
        else:
            logger.debug("消息数据中无 conversation_id")
            return None
    return None


def accept_friend_with_retries(wework_instance, user_id, corp_id):
    result = wework_instance.accept_friend(user_id, corp_id)
    logger.debug(f'result:{result}')


# @wework.msg_register(ntwork.MT_RECV_FRIEND_MSG)
# def friend(wework_instance: ntwork.WeWork, message):
#     data = message["data"]
#     user_id = data["user_id"]
#     corp_id = data["corp_id"]
#     logger.info(f"接收到好友请求，消息内容：{data}")
#     delay = random.randint(1, 180)
#     threading.Timer(delay, accept_friend_with_retries, args=(wework_instance, user_id, corp_id)).start()
#
#     return None


def get_with_retry(get_func, max_retries=5, delay=5):
    retries = 0
    result = None
    while retries < max_retries:
        result = get_func()
        if result:
            break
        logger.warning(f"获取数据失败，重试第{retries + 1}次······")
        retries += 1
        time.sleep(delay)  # 等待一段时间后重试
    return result


@singleton
class WeworkChannel(ChatChannel):
    NOT_SUPPORT_REPLYTYPE = []

    def __init__(self):
        super().__init__()

    def startup(self):
        smart = conf().get("wework_smart", True)
        wework.open(smart)
        logger.info("等待登录······")
        wework.wait_login()
        login_info = wework.get_login_info()
        self.user_id = login_info['user_id']
        self.name = login_info['nickname']
        logger.info(f"登录信息:>>>user_id:{self.user_id}>>>>>>>>name:{self.name}")
        logger.info("静默延迟60s，等待客户端刷新数据，请勿进行任何操作······")
        time.sleep(60)
        contacts = get_with_retry(wework.get_external_contacts)
        rooms = get_with_retry(wework.get_rooms)
        directory = os.path.join(os.getcwd(), "tmp")
        if not contacts or not rooms:
            logger.error("获取contacts或rooms失败，程序退出")
            ntwork.exit_()
            os.exit(0)
        if not os.path.exists(directory):
            os.makedirs(directory)
        # 将contacts保存到json文件中
        with open(os.path.join(directory, 'wework_contacts.json'), 'w', encoding='utf-8') as f:
            json.dump(contacts, f, ensure_ascii=False, indent=4)
        with open(os.path.join(directory, 'wework_rooms.json'), 'w', encoding='utf-8') as f:
            json.dump(rooms, f, ensure_ascii=False, indent=4)
        # 创建一个空字典来保存结果
        result = {}

        # 遍历列表中的每个字典
        for room in rooms['room_list']:
            # 获取聊天室ID
            room_wxid = room['conversation_id']

            # 获取聊天室成员
            room_members = wework.get_room_members(room_wxid)

            # 将聊天室成员保存到结果字典中
            result[room_wxid] = room_members

        # 将结果保存到json文件中
        with open(os.path.join(directory, 'wework_room_members.json'), 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=4)
        logger.info("wework程序初始化完成········")
        run.forever()

    @time_checker
    @_check
    def handle_single(self, cmsg: ChatMessage):
        if cmsg.from_user_id == cmsg.to_user_id:
            # ignore self reply
            return

        sender_uid = cmsg.actual_user_id or cmsg.other_user_id or ""
        logger.info("[WX][单聊] 收到消息 user_id={} nickname={} content={}".format(
            sender_uid, cmsg.other_user_nickname, cmsg.content
        ))

        user_id_black_list = conf().get("user_id_black_list", [])
        if sender_uid and sender_uid in user_id_black_list:
            logger.info("[WX][单聊] user_id={} 在黑名单中，跳过不回复".format(sender_uid))
            return

        if cmsg.ctype == ContextType.VOICE:
            if not conf().get("speech_recognition"):
                return
            logger.debug("[WX]receive voice msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.IMAGE:
            logger.debug("[WX]receive image msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.PATPAT:
            logger.debug("[WX]receive patpat msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.TEXT:
            logger.debug("[WX]receive text msg: {}, cmsg={}".format(json.dumps(cmsg._rawmsg, ensure_ascii=False), cmsg))
        else:
            logger.debug("[WX]receive msg: {}, cmsg={}".format(cmsg.content, cmsg))
        context = self._compose_context(cmsg.ctype, cmsg.content, isgroup=False, msg=cmsg)
        if context:
            self.produce(context)

    @time_checker
    @_check
    def handle_group(self, cmsg: ChatMessage):
        # 过滤机器人自己发出的消息，避免死循环
        login_info = cmsg.wework.get_login_info() if hasattr(cmsg, 'wework') else {}
        self_id = login_info.get('user_id', '') if login_info else ''
        if self_id and cmsg.actual_user_id == self_id:
            logger.debug("[WX]skip self message in group")
            return
        # 从原始消息中读取 appinfo，判断发送者类型：
        # 企业微信内部用户 appinfo 为 base64 字符串（含字母）
        # 个人微信外部用户 appinfo 为纯数字字符串
        appinfo = cmsg._rawmsg.get('data', {}).get('appinfo', '')
        is_internal_user = bool(appinfo) and not appinfo.isdigit()
        user_type = "企业微信内部用户" if is_internal_user else "个人微信用户"
        logger.info("[WX][群消息] msgId={} 群ID={} 群名={} 发送人ID={} 发送人昵称={} 用户类型={} appinfo={} 内容={}".format(
            cmsg.msg_id,
            cmsg.other_user_id, cmsg.other_user_nickname,
            cmsg.actual_user_id, cmsg.actual_user_nickname,
            user_type, appinfo,
            cmsg.content
        ))
        group_id = cmsg.other_user_id
        silence_seconds = conf().get("wework_internal_user_silence_seconds", 180)

        if is_internal_user:
            # 内部用户发言：更新静默计时器，转发给服务（用于会话记录），但不发回复
            # 例外：@ 了机器人，正常回复
            _internal_user_last_msg_time[group_id] = time.time()
            if cmsg.is_at:
                logger.info("[WX][群消息] 内部用户 {} @ 了机器人，忽略静默，正常回复".format(cmsg.actual_user_nickname))
                silence_mode = False
            else:
                logger.info("[WX][群消息] 内部用户 {} 发言，群 {} 进入 {}s 静默，消息将转发服务".format(
                    cmsg.actual_user_nickname, group_id, silence_seconds))
                silence_mode = True
        else:
            # 外部用户发言：检查该群是否在静默期（静默期内仍转发给服务，但不发回复）
            # 例外：@ 了机器人，无论静默与否都正常回复
            last_internal_time = _internal_user_last_msg_time.get(group_id, 0)
            elapsed = time.time() - last_internal_time
            if cmsg.is_at:
                silence_mode = False
                logger.info("[WX][群消息] 消息 @ 了机器人，忽略静默状态，正常回复")
            else:
                silence_mode = elapsed < silence_seconds
                if silence_mode:
                    remaining = int(silence_seconds - elapsed)
                    logger.info("[WX][群消息] 群 {} 静默中（还剩 {}s），消息将转发服务但不发送回复".format(group_id, remaining))

        # @ 了他人（非机器人）：说明这条消息是发给真人的，不回复
        # 检查 at_list 字段（普通消息）
        raw_at_list = cmsg._rawmsg.get('data', {}).get('at_list', [])
        if raw_at_list and not cmsg.is_at:
            at_names = [a.get('nickname', '') for a in raw_at_list]
            logger.info("[WX][群消息] @ 了他人 {}，机器人不介入".format(at_names))
            return
        # 检查文本内容中的 @（引用消息的 @mention 不在 at_list 里，只在文本中）
        if not cmsg.is_at and cmsg.ctype == ContextType.TEXT:
            import re as _re
            at_in_content = _re.findall(r'@(\S+?)[\u2005\u0020]', cmsg.content or '')
            if at_in_content:
                logger.info("[WX][群消息] 内容中检测到 @{} （非机器人），机器人不介入".format(at_in_content))
                return
        if cmsg.ctype == ContextType.VOICE:
            if not conf().get("speech_recognition"):
                return
            logger.debug("[WX]receive voice for group msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.IMAGE:
            logger.debug("[WX]receive image for group msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.JOIN_GROUP:
            nickname = cmsg.actual_user_nickname or "新成员"
            group_id = cmsg.other_user_id
            owner_id = getattr(cmsg, 'group_owner_id', '')
            owner_name = getattr(cmsg, 'group_owner_name', '')
            logger.info(f"[WX][入群欢迎] 群={group_id} 新成员={nickname} 群主={owner_name}({owner_id})")
            welcome_msg = conf().get("group_welcome_msg", "")
            at_list = []
            if welcome_msg:
                # {owner} 替换为 @owner_name，并加入 at_list 实现真正的 @
                if "{owner}" in welcome_msg and owner_id:
                    text = welcome_msg.replace("{nickname}", nickname).replace("{owner}", f"@{owner_name}")
                    at_list = [owner_id]
                else:
                    text = welcome_msg.replace("{nickname}", nickname).replace("{owner}", owner_name)
            else:
                text = f"欢迎 {nickname} 加入群聊！😊"
            wework.send_text(group_id, text)
            return
        elif cmsg.ctype == ContextType.PATPAT:
            logger.debug("[WX]receive note msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.TEXT:
            pass
        else:
            logger.debug("[WX]receive group msg: {}".format(cmsg.content))
        context = self._compose_context(cmsg.ctype, cmsg.content, isgroup=True, msg=cmsg)
        if context:
            if silence_mode:
                context["silence_mode"] = True
            context["is_internal_user"] = is_internal_user
            context["is_at"] = cmsg.is_at
            self.produce(context)

    # 统一的发送函数，每个Channel自行实现，根据reply的type字段发送不同类型的消息
    def send(self, reply: Reply, context: Context):
        if context and context.get("silence_mode"):
            logger.info("[WX][静默模式] 群 {} 静默期内，已调用服务但不发送回复".format(context.get("receiver")))
            return
        # 实时检查：处理期间如果客服插话导致进入静默，也不发送
        if context and context.get("isgroup"):
            group_id = context.get("receiver")
            silence_seconds = conf().get("wework_internal_user_silence_seconds", 180)
            last_internal_time = _internal_user_last_msg_time.get(group_id, 0)
            elapsed = time.time() - last_internal_time
            if elapsed < silence_seconds and not context.get("is_at"):
                logger.info("[WX][静默模式] 群 {} 处理期间客服发言，取消发送（还剩 {}s）".format(
                    group_id, int(silence_seconds - elapsed)))
                return
        if reply and reply.type == ReplyType.TEXT and not reply.content:
            logger.info("[WX] 服务返回全空，跳过发送")
            return
        logger.debug(f"context: {context}")
        receiver = context["receiver"]
        actual_user_id = context["msg"].actual_user_id
        if reply.type == ReplyType.TEXT or reply.type == ReplyType.TEXT_:
            content = re.sub(r"^@(.*?)\n", "", reply.content)
            wework.send_text(receiver, content)
            logger.info("[WX] sendMsg={}, receiver={}".format(reply, receiver))
        elif reply.type == ReplyType.ERROR or reply.type == ReplyType.INFO:
            wework.send_text(receiver, reply.content)
            logger.info("[WX] sendMsg={}, receiver={}".format(reply, receiver))
        elif reply.type == ReplyType.IMAGE:  # 从文件读取图片
            image_storage = reply.content
            image_storage.seek(0)
            # Read data from image_storage
            data = image_storage.read()
            # Create a temporary file
            
            with tempfile.NamedTemporaryFile(delete=False) as temp:
                temp_path = temp.name
                temp.write(data)
            # Send the image
            wework.send_image(receiver, temp_path)
            logger.info("[WX] sendImage, receiver={}".format(receiver))
            # Remove the temporary file
            os.remove(temp_path)
        elif reply.type == ReplyType.IMAGE_URL:  # 从网络下载图片
            img_url = reply.content
            filename = str(uuid.uuid4())

            # 调用你的函数，下载图片并保存为本地文件
            image_path = download_and_compress_image(img_url, filename)

            wework.send_image(receiver, file_path=image_path)
            logger.info("[WX] sendImage url={}, receiver={}".format(img_url, receiver))
        elif reply.type == ReplyType.LINK_CARD:
            card = reply.content if isinstance(reply.content, dict) else {}
            title = card.get("title") or card.get("url") or "链接"
            desc = card.get("desc") or ""
            url = card.get("url")
            image_url = card.get("image_url") or ""
            if not url:
                wework.send_text(receiver, "链接卡片缺少 url")
            else:
                wework.send_link_card(receiver, title, desc, url, image_url)
                logger.info("[WX] sendLinkCard title={}, url={}, receiver={}".format(title, url, receiver))
        elif reply.type == ReplyType.GIF_URL:
            gif_url = reply.content
            filename = str(uuid.uuid4()) + ".gif"
            directory = os.path.join(os.getcwd(), "tmp")
            if not os.path.exists(directory):
                os.makedirs(directory)
            gif_path = os.path.join(directory, filename)
            try:
                resp = requests.get(gif_url, stream=True, timeout=30)
                with open(gif_path, "wb") as f:
                    for block in resp.iter_content(1024):
                        f.write(block)
                wework.send_gif(receiver, gif_path)
                logger.info("[WX] sendGif url={}, receiver={}".format(gif_url, receiver))
            except Exception as e:
                logger.error("[WX] sendGif failed: {}".format(e))
                wework.send_text(receiver, "GIF 发送失败")
            finally:
                if os.path.exists(gif_path):
                    os.remove(gif_path)
        elif reply.type == ReplyType.VIDEO_URL:
            video_url = reply.content
            filename = str(uuid.uuid4())
            video_path = download_video(video_url, filename)

            if video_path is None:
                # 如果视频太大，下载可能会被跳过，此时 video_path 将为 None
                wework.send_text(receiver, "抱歉，视频太大了！！！")
            else:
                wework.send_video(receiver, video_path)
            logger.info("[WX] sendVideo, receiver={}".format(receiver))
        elif reply.type == ReplyType.VOICE:
            current_dir = os.getcwd()
            voice_file = reply.content.split("/")[-1]
            reply.content = os.path.join(current_dir, "tmp", voice_file)
            wework.send_file(receiver, reply.content)
            logger.info("[WX] sendFile={}, receiver={}".format(reply.content, receiver))
