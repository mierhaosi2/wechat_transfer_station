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
  "group_id": "群ID（群聊时）",
  "group_name": "群名（群聊时）"
}
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
