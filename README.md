# dify-on-wechat（企业微信 + Webhook）

将企业微信机器人的消息转发给外部 HTTP 服务（Webhook），并将返回内容发送给用户。

## 架构说明

```
企业微信 → WeworkChannel → WebhookBot → 外部服务（如 Dify、自建接口）
```

- **channel**: `wework` — 接收企业微信消息
- **bot**: `webhook` — 将消息 POST 给外部接口，取回复内容

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

复制并修改 `config-template.json` 为 `config.json`：

```json
{
  "channel_type": "wework",
  "model": "webhook",
  "msg_webhook_url": "http://your-service/api/callback",
  "msg_webhook_timeout": 60,
  "single_chat_prefix": [""],
  "single_chat_reply_prefix": "",
  "group_chat_prefix": [""],
  "group_name_white_list": ["ALL_GROUP"]
}
```

| 字段 | 说明 |
|------|------|
| `channel_type` | 固定为 `wework` |
| `model` | 固定为 `webhook` |
| `msg_webhook_url` | 外部服务接口地址 |
| `msg_webhook_timeout` | 请求超时秒数，默认 60 |
| `single_chat_prefix` | 私聊触发前缀，`[""]` 表示全部触发 |
| `group_chat_prefix` | 群聊触发前缀 |
| `group_name_white_list` | 群名白名单，`["ALL_GROUP"]` 表示全部群 |

### 3. 启动

```bash
python app.py
```

---

## Webhook 协议

### 请求（POST JSON）

```json
{
  "msg_id": "消息ID",
  "query": "用户消息内容",
  "session_id": "会话ID",
  "is_group": false,
  "sender_id": "发送人ID",
  "sender_name": "发送人昵称",
  "is_group_owner": false,
  "group_id": "群ID（群聊时）",
  "group_name": "群名（群聊时）",
  "group_owner_id": "群主ID（群聊时）",
  "group_owner_name": "群主昵称（群聊时）"
}
```

群聊时若发送人是群主，`is_group_owner` 为 `true`（供外部服务识别群主指令，如关闭开场白、关闭自动回复改为 @ 才回复等）。

也可在 `config.json` 配置白名单，名单内 user_id 固定视为群主：

```json
"wework_group_owner_whitelist": ["7881299895970606"]
```

### 响应（JSON）

**纯文本（默认）**

```json
{
  "reply": "回复内容"
}
```

**图片**

```json
{
  "reply_type": "image",
  "image_url": "https://example.com/a.png"
}
```

**链接 / 图文卡片**（标题 + 描述 + 链接 + 封面图，由企微以卡片形式展示）

```json
{
  "reply_type": "link_card",
  "title": "文章标题",
  "desc": "摘要描述",
  "url": "https://example.com/article",
  "image_url": "https://example.com/cover.jpg"
}
```

也支持 `reply_type` 为 `link`，或只返回 `url`（可选 `title`、`desc`、`image_url`）。  
文本字段仍兼容 `content` / `message`。

**文本 + 多张图片**（先发文字，再按 `image_urls` 顺序发图）

```json
{
  "reply_type": "text",
  "reply": "您好，关于 WebPortal 充值流程...",
  "image_urls": [
    "https://example.com/page9_blk0.png",
    "https://example.com/page9_blk1.png"
  ]
}
```

`image_urls` 最多发送 `max_media_send_count` 张（默认 3），每条间隔 `media_send_interval` 秒（默认 1）。也可只传 `image_urls` 不发文字。

---

## 主动推送（单聊纯文本）

与 `msg_webhook_url` 类似，在 `config.json` 里配一个完整 URL 即可。区别是：webhook 是本服务去调外部；`msg_push_url` 是本服务对外开放的推送地址（供 Dify 来调）。

```json
"msg_push_url": "http://127.0.0.1:9899/push/text"
```

上域名后可不写端口，例如：

```json
"msg_push_url": "https://aiapi.example.com/flp-fai-api/v1/wechat/bot-push"
```

为空则不启用。URL 未带端口时，本进程默认监听 `9899`，由 Nginx 等把域名反代进来即可。

### 请求

单条：

```http
POST http://本机IP:9899/push/text
Content-Type: application/json

{
  "receiver_name": "张三",
  "content": "要推送的文本"
}
```

批量（`messages` 数组，也支持顶层直接传数组）：

```json
{
  "messages": [
    {"receiver_name": "张三", "content": "第一条"},
    {"receiver_name": "李四", "content": "第二条"}
  ]
}
```

按外部联系人、内部联系人、群成员缓存查找，字段顺序：`备注 → 昵称 → username → realname → 群昵称 → acctid`；命中唯一一人则发送。

### 响应

单条成功：

```json
{"ok": true, "receiver": "S:xxxxx"}
```

批量（全部成功 `ok=true`；有失败时 HTTP 207，`ok=false`，见 `results`）：

```json
{
  "ok": true,
  "success": 2,
  "failed": 0,
  "results": [
    {"ok": true, "receiver_name": "张三", "receiver": "S:xxxxx"},
    {"ok": true, "receiver_name": "李四", "receiver": "S:yyyyy"}
  ]
}
```

---

## 插件

保留了三个基础插件，如不需要可在 `plugins/plugins.json` 中关闭：

| 插件 | 说明 |
|------|------|
| `Godcmd` | 管理员命令（`#help` 等） |
| `Keyword` | 关键词匹配回复 |
| `Finish` | 会话结束处理 |

---

## Docker 部署

```bash
docker-compose -f docker/docker-compose.yml up -d
```
