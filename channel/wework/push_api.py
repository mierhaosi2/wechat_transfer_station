# encoding:utf-8
"""主动推送文本消息 HTTP 接口（供外部服务调用）"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from channel.wework.run import wework
from common.log import logger
from config import conf

_TMP_DIR = os.path.join(os.getcwd(), "tmp")
_EXTERNAL_CONTACTS_PATH = os.path.join(_TMP_DIR, "wework_contacts.json")
_INNER_CONTACTS_PATH = os.path.join(_TMP_DIR, "wework_inner_contacts.json")
_ROOM_MEMBERS_PATH = os.path.join(_TMP_DIR, "wework_room_members.json")
_NAME_FIELDS = ("remark", "nickname", "username", "realname", "room_nickname", "acctid")
_listen_path = "/push/text"


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


def _load_rooms() -> list:
    """实时拉取群列表，失败则回退缓存。"""
    rooms_path = os.path.join(_TMP_DIR, "wework_rooms.json")
    try:
        rooms = wework.get_rooms()
        if rooms and isinstance(rooms.get("room_list"), list):
            _write_json(rooms_path, rooms)
            return rooms["room_list"]
    except Exception as e:
        logger.warning(f"[PushAPI] 获取群列表失败，回退缓存: {e}")
    data = _read_json(rooms_path) or {}
    return data.get("room_list") or []


def find_rooms_by_name(group_name: str) -> list:
    """按群名精确匹配，返回命中的群列表。"""
    group_name = (group_name or "").strip()
    if not group_name:
        return []
    return [r for r in _load_rooms() if (r.get("nickname") or "").strip() == group_name]


def push_text_to_group(group_id: str, content: str):
    """
    向群发送文本。
    返回 (ok: bool, error: str|None, receiver: str|None)
    """
    if not group_id or not group_id.startswith("R:"):
        return False, "group_id 格式不正确（须以 R: 开头）", None
    wework.send_text(group_id, content)
    return True, None, group_id


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

    content = item.get("content") if item.get("content") is not None else item.get("reply")
    if content is None or str(content) == "":
        return {"ok": False, "error": "缺少 content"}

    # 群聊：直接传 group_id
    group_id = (item.get("group_id") or "").strip()
    if group_id:
        try:
            ok, err, receiver = push_text_to_group(group_id, str(content))
            if ok:
                logger.info(f"[PushAPI] 群推送成功 group_id={group_id}")
                return {"ok": True, "group_id": group_id, "receiver": receiver}
            logger.warning(f"[PushAPI] 群推送失败 group_id={group_id} error={err}")
            return {"ok": False, "group_id": group_id, "error": err}
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
            ok, err, receiver = push_text_to_group(gid, str(content))
            if ok:
                logger.info(f"[PushAPI] 群推送成功 group_name={group_name} group_id={gid}")
                return {"ok": True, "group_name": group_name, "group_id": gid, "receiver": receiver}
            logger.warning(f"[PushAPI] 群推送失败 group_name={group_name} error={err}")
            return {"ok": False, "group_name": group_name, "error": err}
        except Exception as e:
            logger.exception(f"[PushAPI] 群推送异常 group_name={group_name}: {e}")
            return {"ok": False, "group_name": group_name, "error": str(e)}

    # 单聊：按名字查找
    receiver_name = (item.get("receiver_name") or item.get("name") or "").strip()
    if not receiver_name:
        return {"ok": False, "error": "缺少 receiver_name / group_id / group_name"}
    try:
        ok, err, receiver = push_text_by_name(receiver_name, str(content))
        if ok:
            logger.info(f"[PushAPI] 推送成功 receiver_name={receiver_name} receiver={receiver}")
            return {"ok": True, "receiver_name": receiver_name, "receiver": receiver}
        logger.warning(f"[PushAPI] 推送失败 receiver_name={receiver_name} error={err}")
        return {"ok": False, "receiver_name": receiver_name, "error": err}
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
                self._send_json(200, {"ok": True, "receiver": r.get("receiver")})
            else:
                err = r.get("error") or "推送失败"
                status = 400
                if err == "未找到联系人":
                    status = 404
                elif "多人" in err:
                    status = 409
                self._send_json(status, {"ok": False, "error": err})
            return

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
