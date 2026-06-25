# workkk — AI上班模拟器 MCP Server

让 Claude 扮演一个真实的打工人，实时监控他在干什么（写代码、摸鱼、开会……），并把状态展示在赛博感的监控大屏上。

## 快速体验

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

打开 http://localhost:8000 查看监控大屏。

---

## 在 claude.ai 里添加这个 MCP

### 第一步：部署到 Railway

1. 把代码推送到 GitHub
2. 在 [railway.app](https://railway.app) 新建项目 → Deploy from GitHub Repo → 选这个仓库
3. Railway 会自动识别 `railway.toml` 并启动服务
4. 在 Railway 项目设置里找到 **Public Domain**，记下 URL，例如：
   ```
   https://workkk-production.up.railway.app
   ```

### 第二步：在 claude.ai 添加 MCP Server

1. 打开 [claude.ai](https://claude.ai) → 左侧菜单 → **Settings**
2. 找到 **Integrations** 或 **MCP Servers** 选项
3. 点击 **Add MCP Server**，填入：
   ```
   https://workkk-production.up.railway.app/mcp
   ```
4. claude.ai 会自动发起 OAuth 授权流程：
   - 自动完成动态客户端注册 (`/oauth/register`)
   - 跳转到授权页面 (`/oauth/authorize`) — 服务器自动批准，无需登录
   - 完成 PKCE token 交换 (`/oauth/token`)
5. 授权完成后，工具 `work_action` 就可以在对话里使用了

### 第三步：开始玩

在 claude.ai 对话框里输入：

```
你现在是一个真实的打工人，开始你的一天吧！用 work_action 工具执行各种上班动作，
同时把你的内心OS告诉我。记得时不时摸鱼，注意心情和精力值！
```

然后在浏览器里打开监控大屏实时围观。

---

## OAuth 端点说明

| 端点 | 说明 |
|------|------|
| `GET /.well-known/oauth-authorization-server` | OAuth 元数据发现，声明支持动态注册 |
| `POST /oauth/register` | 动态客户端注册（RFC 7591），自动批准 |
| `GET /oauth/authorize` | 授权端点，自动批准并 redirect 带 code |
| `POST /oauth/token` | Token 端点，验证 PKCE S256 后签发 access_token |

## MCP 端点

| 端点 | 说明 |
|------|------|
| `POST /mcp` | Streamable HTTP 传输，处理 JSON-RPC 2.0 请求 |
| `GET /mcp` | SSE 传输（兼容旧版 MCP 客户端） |

## 前端

| 端点 | 说明 |
|------|------|
| `GET /` | 赛博风监控大屏，每 2 秒自动刷新 |
| `GET /status` | 当前游戏状态 JSON |

## 工具说明

### `work_action(action, thought)`

| action | 效果 |
|--------|------|
| `write_code` | 消耗精力，30% 概率触发 bug 事件 |
| `debug` | 消耗精力+心情，必然触发 bug 事件 |
| `slack_off` | 回复精力，摸鱼技能+5，20% 概率被抓 |
| `buy_coffee` | 回复精力，50% 概率回来发现需求变了 |
| `attend_meeting` | 消耗精力+心情，站会变 1 小时 |
| `check_messages` | 小消耗，40% 概率是领导找麻烦 |
| `get_status` | 查看当前状态 |

`thought` 字段是 Claude 的内心 OS，会实时显示在监控大屏上。
