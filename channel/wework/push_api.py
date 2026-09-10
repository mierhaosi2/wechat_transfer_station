# encoding:utf-8
"""主动推送接口（供外部服务调用），支持文本/图片/链接卡片/多图等消息类型"""
import json
import os
import re as _re
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import requests as _requests

from channel.wework.run import wework
from common.log import logger
from config import conf

_TMP_DIR = os.path.join(os.getcwd(), "tmp")
_EXTERNAL_CONTACTS_PATH = os.path.join(_TMP_DIR, "wework_contacts.json")
_INNER_CONTACTS_PATH = os.path.join(_TMP_DIR, "wework_inner_contacts.json")
_ROOM_MEMBERS_PATH = os.path.join(_TMP_DIR, "wework_room_members.json")
_NAME_FIELDS = ("remark", "nickname", "username", "realname", "room_nickname", "acctid")
_listen_path = "/push/text"
_ROOM_GROUPS_PATH = "/push/groups"
_ROOM_CACHE_LOCK = threading.Lock()


def _ensure_tmp_dir():
    if not os.path.exists(_TMP_DIR):
        os.makedirs(_TMP_DIR)


def _read_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, data):
    _ensure_tmp_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def _extract_user_list(payload) -> list:
    if not payload:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("user_list", "contact_list", "member_list"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _load_external_users() -> list:
    try:
        contacts = wework.get_external_contacts()
        if contacts and contacts.get("user_list") is not None:
            _write_json(_EXTERNAL_CONTACTS_PATH, contacts)
            return list(contacts.get("user_list") or [])
    except Exception as e:
        logger.warning(f"[PushAPI] 获取外部联系人失败，回退缓存: {e}")
    return _extract_user_list(_read_json(_EXTERNAL_CONTACTS_PATH))


def _load_inner_users() -> list:
    try:
        contacts = wework.get_inner_contacts()
        if contacts:
            users = _extract_user_list(contacts)
            _write_json(_INNER_CONTACTS_PATH, contacts)
            return users
    except Exception as e:
        logger.warning(f"[PushAPI] 获取内部联系人失败，回退缓存: {e}")
    return _extract_user_list(_read_json(_INNER_CONTACTS_PATH))


def _load_room_member_users() -> list:
    """从初始化缓存的群成员里收集用户（内部同事常见于此）。"""
    rooms = _read_json(_ROOM_MEMBERS_PATH) or {}
    users = []
    if not isinstance(rooms, dict):
        return users
    for room in rooms.values():
        if not isinstance(room, dict):
            continue
        for member in room.get("member_list") or []:
            if isinstance(member, dict):
                users.append(member)
    return users


def _load_all_users() -> list:
    """合并外部联系人、内部联系人、群成员，按 user_id 去重。"""
    merged = {}
    for user in _load_external_users() + _load_inner_users() + _load_room_member_users():
        if not isinstance(user, dict):
            continue
        uid = user.get("user_id") or ""
        key = uid or f"anon:{id(user)}"
        # 优先保留已有 conversation_id 的记录
        old = merged.get(key)
        if not old or (user.get("conversation_id") and not old.get("conversation_id")):
            merged[key] = user
    return list(merged.values())


def _dedupe_by_user_id(users: list) -> list:
    uniq = {}
    for user in users:
        uid = user.get("user_id") or ""
        key = uid or f"anon:{id(user)}"
        if key not in uniq:
            uniq[key] = user
    return list(uniq.values())


def find_contacts_by_name(name: str) -> list:
    """按 remark → nickname → username → realname → room_nickname → acctid 精确匹配。"""
    name = (name or "").strip()
    if not name:
        return []
    user_list = _load_all_users()
    for field in _NAME_FIELDS:
        matched = [u for u in user_list if (u.get(field) or "").strip() == name]
        if matched:
            return _dedupe_by_user_id(matched)
    return []


def _resolve_conversation_id(user: dict) -> str:
    conversation_id = (user.get("conversation_id") or "").strip()
    if conversation_id:
        return conversation_id
    user_id = (user.get("user_id") or "").strip()
    if not user_id:
        return ""
    try:
        login_info = wework.get_login_info() or {}
    except Exception:
        login_info = {}
    self_id = (login_info.get("user_id") or "").strip()
    if not self_id:
        return ""
    # 单聊会话格式与外部联系人一致: S:{self_user_id}_{peer_user_id}
    return f"S:{self_id}_{user_id}"


def push_text_by_name(receiver_name: str, content: str):
    """
    按名字查找单聊联系人并发送文本。
    返回 (ok: bool, error: str|None, receiver: str|None)
    """
    matched = find_contacts_by_name(receiver_name)
    if not matched:
        return False, "未找到联系人", None
    if len(matched) > 1:
        return False, "匹配到多人，请使用更精确的名字", None

    user = matched[0]
    conversation_id = _resolve_conversation_id(user)
    if not conversation_id:
        return False, "联系人缺少 conversation_id", None

    wework.send_text(conversation_id, content)
    return True, None, conversation_id


def _load_rooms(refresh: bool = True) -> list:
    """实时拉取群列表，失败或关闭刷新时回退缓存。"""
    rooms_path = os.path.join(_TMP_DIR, "wework_rooms.json")
    if refresh:
        try:
            rooms = wework.get_rooms()
            if rooms and isinstance(rooms.get("room_list"), list):
                _write_json(rooms_path, rooms)
                return rooms["room_list"]
        except Exception as e:
            logger.warning(f"[PushAPI] 获取群列表失败，回退缓存: {e}")
    data = _read_json(rooms_path) or {}
    return data.get("room_list") or []


def _load_room_members(group_id: str, refresh: bool = True) -> list:
    """按群 ID 获取成员；实时查询失败时回退对应群的本地缓存。"""
    if not group_id:
        return []

    with _ROOM_CACHE_LOCK:
        cached_rooms = _read_json(_ROOM_MEMBERS_PATH) or {}

    if refresh:
        try:
            members = wework.get_room_members(group_id)
            if members and isinstance(members.get("member_list"), list):
                with _ROOM_CACHE_LOCK:
                    cached_rooms[group_id] = members
                    _write_json(_ROOM_MEMBERS_PATH, cached_rooms)
                return members["member_list"]
        except Exception as e:
            logger.warning(f"[PushAPI] 获取群成员失败 group_id={group_id}，回退缓存: {e}")

    members = cached_rooms.get(group_id) or {}
    return members.get("member_list") or []


def _room_payload(room: dict, include_members: bool = False, refresh: bool = True) -> dict:
    """将 ntwork 群对象转换为对外稳定的数据结构。"""
    payload = {
        "conversation_id": room.get("conversation_id") or "",
        "name": room.get("nickname") or "",
        "nickname": room.get("nickname") or "",
        "create_time": room.get("create_time"),
        "create_user_id": room.get("create_user_id") or "",
        "is_creator": bool(room.get("is_creator")),
        "is_admin": bool(room.get("is_admin")),
        "is_external": bool(room.get("is_external")),
        "member_count": room.get("total", 0),
    }
    if include_members:
        members = _load_room_members(payload["conversation_id"], refresh=refresh)
        payload["member_count"] = len(members)
        payload["members"] = members
    return payload


def _find_room(group_id: str, refresh: bool = True):
    """按 conversation_id 查找群，找不到时返回 None。"""
    rooms = _load_rooms(refresh=refresh)
    return next((room for room in rooms if room.get("conversation_id") == group_id), None)


def find_rooms_by_name(group_name: str) -> list:
    """按群名精确匹配，返回命中的群列表。"""
    group_name = (group_name or "").strip()
    if not group_name:
        return []
    return [r for r in _load_rooms() if (r.get("nickname") or "").strip() == group_name]


def _query_bool(query: dict, name: str, default: bool) -> bool:
    """解析 refresh/include_members 这类布尔查询参数。"""
    value = query.get(name, [None])[0]
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _query_optional_bool(query: dict, name: str):
    """解析可选布尔查询参数；参数不存在时返回 None。"""
    if name not in query:
        return None
    return _query_bool(query, name, False)


def _group_api_response(path: str, query: dict):
    """处理群列表和群成员查询，返回 (HTTP 状态码, JSON 数据)。"""
    groups_path = _ROOM_GROUPS_PATH.rstrip("/")
    refresh = _query_bool(query, "refresh", True)

    if path == groups_path:
        include_members = _query_bool(query, "include_members", False)
        is_external = _query_optional_bool(query, "is_external")
        rooms = _load_rooms(refresh=refresh)
        if is_external is not None:
            rooms = [
                room for room in rooms
                if bool(room.get("is_external")) is is_external
            ]
        groups = [
            _room_payload(room, include_members=include_members, refresh=refresh)
            for room in rooms
        ]
        return 200, {
            "ok": True,
            "count": len(groups),
            "refresh": refresh,
            "include_members": include_members,
            "is_external": is_external,
            "groups": groups,
        }

    members_suffix = "/members"
    if path.startswith(groups_path + "/") and path.endswith(members_suffix):
        group_id = unquote(path[len(groups_path) + 1:-len(members_suffix)]).strip()
        if not group_id.startswith("R:"):
            return 400, {"ok": False, "error": "group_id 格式不正确（须以 R: 开头）"}

        room = _find_room(group_id, refresh=refresh)
        if room is None:
            return 404, {"ok": False, "error": "未找到群", "group_id": group_id}

        members = _load_room_members(group_id, refresh=refresh)
        return 200, {
            "ok": True,
            "refresh": refresh,
            "group": _room_payload(room),
            "member_count": len(members),
            "members": members,
        }

    return None


def _get_group_owner_id(group_id: str) -> str:
    """从群列表缓存里查群主 user_id，找不到返回空字符串。"""
    rooms_path = os.path.join(_TMP_DIR, "wework_rooms.json")
    rooms_data = _read_json(rooms_path) or {}
    for room in rooms_data.get("room_list") or []:
        if room.get("conversation_id") == group_id:
            return room.get("create_user_id") or ""
    return ""


def _resolve_at_list(group_id: str, at_list: list, at_names: list) -> list:
    """
    合并 at_list（user_id）和 at_names（名字）→ 最终 user_id 列表。
    at_names 中若命中 wework_group_owner_alias（默认"小李"），
    自动替换为该群群主的 user_id。
    其余名字从群成员缓存里按 room_nickname / username / realname / nickname 查找。
    """
    result = [str(uid) for uid in (at_list or []) if str(uid).strip()]
    owner_alias = (conf().get("wework_group_owner_alias") or "小李").strip()

    if at_names:
        members_data = (_read_json(_ROOM_MEMBERS_PATH) or {}).get(group_id) or {}
        member_list = members_data.get("member_list") or []
        for name in at_names:
            name = (name or "").strip()
            if not name:
                continue

            # 群主别名：直接解析成群主 user_id
            if name == owner_alias:
                owner_id = _get_group_owner_id(group_id)
                if owner_id and owner_id not in result:
                    result.append(owner_id)
                    logger.info(f"[PushAPI] at_names 别名 {name!r} → 群主 {owner_id}（群 {group_id}）")
                else:
                    logger.warning(f"[PushAPI] at_names 别名 {name!r} 未找到群主（群 {group_id}）")
                continue

            # 普通名字：在群成员里匹配
            found = None
            for m in member_list:
                if name in (
                    m.get("room_nickname") or "",
                    m.get("username") or "",
                    m.get("realname") or "",
                    m.get("nickname") or "",
                ):
                    found = m.get("user_id") or ""
                    break
            if found:
                if found not in result:
                    result.append(found)
            else:
                logger.warning(f"[PushAPI] at_names 中未找到成员 {name!r}（群 {group_id}）")
    return result


def push_text_to_group(group_id: str, content: str,
                       at_list: list = None, at_names: list = None):
    """
    向群发送文本，可选真实 @ 成员。
    at_list: user_id 列表（直接使用）
    at_names: 名字列表（在群成员里查成 user_id；别名如"小李"自动转为群主）
    返回 (ok: bool, error: str|None, receiver: str|None)
    """
    if not group_id or not group_id.startswith("R:"):
        return False, "group_id 格式不正确（须以 R: 开头）", None

    resolved = _resolve_at_list(group_id, at_list, at_names)

    # 把 at_names 里的名字从 content 中去掉（避免出现 @小李 之类的纯文字）
    clean_content = content
    for name in (at_names or []):
        name = (name or "").strip()
        if not name:
            continue
        import re as _re
        # 去掉 @别名 及其后的空白/特殊空格
        clean_content = _re.sub(
            r'@' + _re.escape(name) + r'[\u2005\u0020]*',
            '',
            clean_content
        ).strip()

    if resolved:
        wework.send_room_at_msg(group_id, clean_content, resolved)
    else:
        wework.send_text(group_id, clean_content)
    return True, None, group_id


# ---------- 通用多类型发送 ----------

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


def _download_image(url: str) -> str:
    """下载图片到临时文件，返回本地路径。不立即删除，由调用方决定清理时机。"""
    directory = os.path.join(os.getcwd(), "tmp")
    if not os.path.exists(directory):
        os.makedirs(directory)
    filename = os.path.join(directory, str(uuid.uuid4()) + ".jpg")
    resp = _requests.get(url, stream=True, timeout=30)
    resp.raise_for_status()
    with open(filename, "wb") as f:
        for block in resp.iter_content(1024):
            f.write(block)
    return filename


def _send_image_url(receiver: str, url: str):
    """下载图片并发送，等企微完成后再清理临时文件。"""
    path = _download_image(url)
    wework.send_image(receiver, file_path=path)
    # ntwork 是异步把文件路径交给企微进程上传，需等上传完成再删
    # 此处等待 3 秒，足够企微读取并上传完毕
    def _cleanup():
        time.sleep(3)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
    threading.Thread(target=_cleanup, daemon=True).start()


def _download_file(url: str, filename: str = None) -> str:
    """下载任意文件到临时目录，返回本地路径。"""
    directory = os.path.join(os.getcwd(), "tmp")
    if not os.path.exists(directory):
        os.makedirs(directory)
    if not filename:
        # 从 URL 提取文件名，失败则用 UUID
        raw_name = url.rstrip("/").split("/")[-1].split("?")[0]
        filename = raw_name if raw_name else str(uuid.uuid4())
    local_path = os.path.join(directory, filename)
    resp = _requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        for block in resp.iter_content(8192):
            f.write(block)
    return local_path


def _send_file(receiver: str, file_path: str, delete_after: bool = False):
    """发送本地文件，可选发送后延迟删除。"""
    wework.send_file(receiver, file_path)
    if delete_after:
        def _cleanup():
            time.sleep(5)
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass
        threading.Thread(target=_cleanup, daemon=True).start()


def send_to_receiver(receiver: str, item: dict,
                     at_list: list = None, at_names: list = None):
    """
    向 receiver（单聊 S:xxx 或群 R:xxx）发送消息。
    item 格式与 webhook 响应一致，支持：
      纯文本: {"reply":"..."} / {"content":"..."}
      图片:   {"reply_type":"image","image_url":"..."}
      链接卡片: {"reply_type":"link_card","title":"...","url":"..."}
      文本+多图: {"reply":"...","image_urls":["..."]}
    群聊时可传 at_list/at_names 触发真实 @。
    """
    is_group = receiver.startswith("R:")
    reply_type = (item.get("reply_type") or item.get("type") or "").lower().strip()
    image_urls = _collect_image_urls(item)

    def _send_text(text: str):
        if is_group and (at_list or at_names):
            resolved = _resolve_at_list(receiver, at_list, at_names)
            clean = text
            for name in (at_names or []):
                name = (name or "").strip()
                if name:
                    clean = _re.sub(r'@' + _re.escape(name) + r'[\u2005\u0020]*', '', clean).strip()
            if resolved:
                # 企微 @ 消息格式：@nickname 后须有分隔符，否则正文会粘连
                if clean and not clean.startswith(('\u2005', ' ')):
                    clean = '\u2005' + clean
                wework.send_room_at_msg(receiver, clean, resolved)
                return
        wework.send_text(receiver, text)

    if reply_type in ("file","file_url"):
        file_url = item.get("file_url") or item.get("url")
        file_path = item.get("file_path")
        if file_path:
            if os.path.exists(file_path):
                _send_file(receiver, file_path)
            else:
                logger.warning(f"[PushAPI] 文件不存在: {file_path}")
        elif file_url:
            try:
                filename = item.get("filename")
                local_path = _download_file(file_url, filename)
                _send_file(receiver, local_path, delete_after=True)
                logger.info(f"[PushAPI] 下载并发送文件 url={file_url} receiver={receiver}")
            except Exception as e:
                logger.error(f"[PushAPI] 下载文件失败 url={file_url}: {e}")
        else:
            logger.warning(f"[PushAPI] file 类型消息缺少 file_url 或 file_path")
        return

    # 图片
    if reply_type in ("image", "image_url"):
        img_url = item.get("image_url") or item.get("url")
        if img_url:
            _send_image_url(receiver, img_url)
        return

    # 链接卡片
    if reply_type in ("link_card", "link", "card", "sharing"):
        card = item.get("link_card") or item
        url = card.get("url") or item.get("url")
        if not url:
            wework.send_text(receiver, "链接卡片缺少 url")
            return
        title = card.get("title") or url
        desc = card.get("desc") or card.get("description") or ""
        image_url = card.get("image_url") or ""
        text_desc = card.get("text_desc") or ""
        if text_desc:
            wework.send_text(receiver, text_desc)
            time.sleep(1)
        wework.send_link_card(receiver, title, desc, url, image_url)
        return

    # 仅有 url，无文字 → 卡片
    if item.get("url") and not item.get("reply") and not item.get("content"):
        url = item["url"]
        wework.send_link_card(receiver, item.get("title") or url,
                              item.get("desc") or "", url, item.get("image_url") or "")
        return

    # 文本 + 多图
    text = item.get("reply") or item.get("content") or item.get("message")
    max_images = conf().get("max_media_send_count", 3)
    if text:
        _send_text(str(text))
    if image_urls:
        for url in image_urls[:max_images]:
            time.sleep(0.5)
            _send_image_url(receiver, url)
        return

    # 纯图片（无文字）
    if not text and image_urls:
        for url in image_urls[:max_images]:
            time.sleep(0.5)
            _send_image_url(receiver, url)
        return

    # 纯文本兜底
    if text:
        return  # 已在上面发送
    # 全空
    logger.info(f"[PushAPI] 消息全空，跳过发送 receiver={receiver}")


def _normalize_push_items(data) -> list:
    """
    统一成消息项列表，每项支持单聊或群聊：
    1) 单条: {"receiver_name":"张三","content":"..."}
               {"group_id":"R:xxx","content":"..."}
               {"group_name":"群名","content":"..."}
    2) 批量: {"messages":[...]}
    3) 顶层数组: [...]
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("messages"), list):
        return data["messages"]
    return [data]


def _push_one_item(item: dict) -> dict:
    if not isinstance(item, dict):
        return {"ok": False, "error": "消息项须为 JSON 对象"}

    at_list = item.get("at_list") or []
    at_names = item.get("at_names") or []

    # 群聊：直接传 group_id
    group_id = (item.get("group_id") or "").strip()
    if group_id:
        if not group_id.startswith("R:"):
            return {"ok": False, "group_id": group_id, "error": "group_id 格式不正确（须以 R: 开头）"}
        try:
            send_to_receiver(group_id, item, at_list=at_list, at_names=at_names)
            logger.info(f"[PushAPI] 群推送成功 group_id={group_id}")
            return {"ok": True, "group_id": group_id, "receiver": group_id}
        except Exception as e:
            logger.exception(f"[PushAPI] 群推送异常 group_id={group_id}: {e}")
            return {"ok": False, "group_id": group_id, "error": str(e)}

    # 群聊：按群名查找
    group_name = (item.get("group_name") or "").strip()
    if group_name:
        try:
            matched_rooms = find_rooms_by_name(group_name)
            if not matched_rooms:
                logger.warning(f"[PushAPI] 群推送失败 group_name={group_name} error=未找到群")
                return {"ok": False, "group_name": group_name, "error": "未找到群"}
            if len(matched_rooms) > 1:
                logger.warning(f"[PushAPI] 群推送失败 group_name={group_name} error=匹配到多个群")
                return {"ok": False, "group_name": group_name, "error": "匹配到多个群，请使用 group_id"}
            gid = matched_rooms[0]["conversation_id"]
            send_to_receiver(gid, item, at_list=at_list, at_names=at_names)
            logger.info(f"[PushAPI] 群推送成功 group_name={group_name} group_id={gid}")
            return {"ok": True, "group_name": group_name, "group_id": gid, "receiver": gid}
        except Exception as e:
            logger.exception(f"[PushAPI] 群推送异常 group_name={group_name}: {e}")
            return {"ok": False, "group_name": group_name, "error": str(e)}

    # 单聊：直接传 receiver_id（user_id），通过联系人缓存或 login_info 拼 conversation_id
    receiver_id = (item.get("receiver_id") or "").strip()
    if receiver_id:
        try:
            # 先在联系人里找，拿到带 conversation_id 的 user 对象
            all_users = _load_all_users()
            matched_by_id = [u for u in all_users if (u.get("user_id") or "").strip() == receiver_id]
            if matched_by_id:
                cid = _resolve_conversation_id(matched_by_id[0])
            else:
                try:
                    login_info = wework.get_login_info() or {}
                except Exception:
                    login_info = {}
                self_id = (login_info.get("user_id") or "").strip()
                cid = f"S:{self_id}_{receiver_id}" if self_id else ""
            if not cid:
                return {"ok": False, "receiver_id": receiver_id, "error": "无法解析 conversation_id，请检查登录状态"}
            send_to_receiver(cid, item, at_list=at_list, at_names=at_names)
            logger.info(f"[PushAPI] 推送成功 receiver_id={receiver_id} conversation_id={cid}")
            return {"ok": True, "receiver_id": receiver_id, "receiver": cid}
        except Exception as e:
            logger.exception(f"[PushAPI] 推送异常 receiver_id={receiver_id}: {e}")
            return {"ok": False, "receiver_id": receiver_id, "error": str(e)}

    # 单聊：按名字查找
    receiver_name = (item.get("receiver_name") or item.get("name") or "").strip()
    if not receiver_name:
        return {"ok": False, "error": "缺少 receiver_name / receiver_id / group_id / group_name"}
    try:
        matched = find_contacts_by_name(receiver_name)
        if not matched:
            return {"ok": False, "receiver_name": receiver_name, "error": "未找到联系人"}
        if len(matched) > 1:
            return {"ok": False, "receiver_name": receiver_name, "error": "匹配到多人，请使用更精确的名字"}
        conversation_id = _resolve_conversation_id(matched[0])
        if not conversation_id:
            return {"ok": False, "receiver_name": receiver_name, "error": "联系人缺少 conversation_id"}
        send_to_receiver(conversation_id, item)
        logger.info(f"[PushAPI] 推送成功 receiver_name={receiver_name} receiver={conversation_id}")
        return {"ok": True, "receiver_name": receiver_name, "receiver": conversation_id}
    except Exception as e:
        logger.exception(f"[PushAPI] 推送异常 receiver_name={receiver_name}: {e}")
        return {"ok": False, "receiver_name": receiver_name, "error": str(e)}


class _PushHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.debug("[PushAPI] %s - %s" % (self.address_string(), format % args))

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        query = parse_qs(urlparse(self.path).query)
        result = _group_api_response(path, query)
        if result is None:
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        status, payload = result
        logger.info(f"[PushAPI] 群信息查询 path={path} refresh={query.get('refresh', ['true'])[0]}")
        self._send_json(status, payload)

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        expect = _listen_path.rstrip("/") or "/"
        if path != expect:
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            self._send_json(400, {"ok": False, "error": f"无效 JSON: {e}"})
            return

        logger.info(f"[PushAPI] 收到推送请求 from={self.client_address[0]} body={json.dumps(data, ensure_ascii=False)}")

        items = _normalize_push_items(data)
        if not items:
            self._send_json(400, {"ok": False, "error": "请求体须为对象、messages 数组或消息数组"})
            return

        results = [_push_one_item(item) for item in items]
        all_ok = all(r.get("ok") for r in results)

        # 单条保持原响应形状，批量返回 results
        if len(results) == 1 and not (
            isinstance(data, list) or (isinstance(data, dict) and isinstance(data.get("messages"), list))
        ):
            r = results[0]
            if r.get("ok"):
                logger.info(f"[PushAPI] 推送完成 receiver={r.get('receiver')}")
                self._send_json(200, {"ok": True, "receiver": r.get("receiver")})
            else:
                err = r.get("error") or "推送失败"
                logger.warning(f"[PushAPI] 推送失败 error={err}")
                status = 400
                if err == "未找到联系人":
                    status = 404
                elif "多人" in err:
                    status = 409
                self._send_json(status, {"ok": False, "error": err})
            return

        logger.info(f"[PushAPI] 批量推送完成 total={len(results)} success={sum(1 for r in results if r.get('ok'))} failed={sum(1 for r in results if not r.get('ok'))}")
        self._send_json(
            200 if all_ok else 207,
            {
                "ok": all_ok,
                "success": sum(1 for r in results if r.get("ok")),
                "failed": sum(1 for r in results if not r.get("ok")),
                "results": results,
            },
        )


def _get_local_ip() -> str:
    """获取本机局域网 IP，失败时返回 127.0.0.1。"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def start_push_api():
    """按 msg_push_url 在后台线程启动推送 HTTP 服务。"""
    global _listen_path
    push_url = (conf().get("msg_push_url") or "").strip()
    if not push_url:
        logger.info("[PushAPI] msg_push_url 未配置，跳过启动")
        return

    parsed = urlparse(push_url)
    bind_host = "0.0.0.0"
    port = parsed.port or 9899
    path = parsed.path.rstrip("/") or "/push/text"
    _listen_path = path

    def _run():
        try:
            server = ThreadingHTTPServer((bind_host, int(port)), _PushHandler)
            local_ip = _get_local_ip()
            logger.info(
                f"[PushAPI] 主动推送接口已启动: 监听 {bind_host}:{port}{path}"
                f" ，本机可访问地址 http://{local_ip}:{port}{path}"
            )
            server.serve_forever()
        except Exception as e:
            logger.error(f"[PushAPI] 启动失败: {e}")

    t = threading.Thread(target=_run, name="wework-push-api", daemon=True)
    t.start()
