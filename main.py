#!/usr/bin/env python3
"""AI上班模拟器 MCP Server — workkk"""

import asyncio, base64, hashlib, json, os, random, secrets, time

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    HTMLResponse, JSONResponse, Response, RedirectResponse, StreamingResponse,
)

app = FastAPI(title="AI上班模拟器")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# ── In-memory stores ───────────────────────────────────────────────────────────
_clients: dict = {}   # client_id → {client_secret, redirect_uris}
_codes:   dict = {}   # code      → {client_id, code_challenge, redirect_uri, exp}
_tokens:  dict = {}   # token     → {client_id, exp}

# ── Game state ─────────────────────────────────────────────────────────────────
_s: dict = {
    "mood":           100,
    "energy":         100,
    "slacking_skill": 0,
    "current_status": "刚刚打卡，准备开始摸鱼",
    "last_event":     "元气满满地来上班了",
    "thought":        "今天一定要准时下班",
    "log":            [],
}

_BUGS = [
    "找了2小时，发现代码没push",
    "Python缩进多了一格",
    "调了半天UI，是OS字体调大了",
    "重启了一下，好了，原因不明",
    "发现是在注释里改的代码",
]
_BOSS = [
    "领导问为什么没上线，他自己没审批",
    "站会说'就一个小需求'，涉及三个系统",
    "被莫名批评，可能早饭没吃好",
]
_CLIENT = [
    "就改个颜色，结果整套设计稿重来",
    "线上炸了，是别人写的代码，来找我修",
]

_TOOL = {
    "name": "work_action",
    "description": (
        "执行AI打工人的上班动作。每次行动都会更新状态并可能触发随机事件。"
        "用 thought 字段说出你的内心OS，它会实时显示在监控大屏上。"
    ),
    "inputSchema": {
        "type": "object",
        "required": ["action", "thought"],
        "properties": {
            "action": {
                "type": "string",
                "description": "要执行的动作",
                "enum": [
                    "write_code", "debug", "slack_off", "buy_coffee",
                    "attend_meeting", "check_messages", "get_status",
                ],
            },
            "thought": {
                "type": "string",
                "description": "你此刻的内心独白，会实时显示在监控大屏上",
            },
        },
    },
}

# ── Game logic ─────────────────────────────────────────────────────────────────
def _c(v: int) -> int:
    return max(0, min(100, v))

def work_action(action: str, thought: str) -> dict:
    _s["thought"] = thought
    event = ""
    ts = time.strftime("%H:%M:%S")

    if action == "write_code":
        _s["current_status"] = "敲代码中 💻"
        _s["energy"] = _c(_s["energy"] - 10)
        if random.random() < 0.3:
            event = random.choice(_BUGS)
            _s["mood"] = _c(_s["mood"] - 15)
        else:
            _s["mood"] = _c(_s["mood"] + 5)

    elif action == "debug":
        _s["current_status"] = "修Bug中 🐛"
        _s["energy"] = _c(_s["energy"] - 15)
        event = random.choice(_BUGS)
        _s["mood"] = _c(_s["mood"] - 10)

    elif action == "slack_off":
        _s["current_status"] = "摸鱼中 🐟"
        _s["energy"] = _c(_s["energy"] + 20)
        _s["slacking_skill"] = min(999, _s["slacking_skill"] + 5)
        if random.random() < 0.2:
            event = random.choice(_BOSS)
            _s["mood"] = _c(_s["mood"] - 25)
        else:
            _s["mood"] = _c(_s["mood"] + 10)

    elif action == "buy_coffee":
        _s["current_status"] = "下楼买咖啡 ☕"
        _s["energy"] = _c(_s["energy"] + 15)
        if random.random() < 0.5:
            event = random.choice(_CLIENT)
            _s["mood"] = _c(_s["mood"] - 20)
        else:
            _s["mood"] = _c(_s["mood"] + 8)

    elif action == "attend_meeting":
        _s["current_status"] = "开会中 📊"
        _s["energy"] = _c(_s["energy"] - 20)
        _s["mood"] = _c(_s["mood"] - 10)
        event = "站会说15分钟，开了整整1小时"

    elif action == "check_messages":
        _s["current_status"] = "看消息 💬"
        _s["energy"] = _c(_s["energy"] - 5)
        if random.random() < 0.4:
            event = random.choice(_BOSS)
            _s["mood"] = _c(_s["mood"] - 15)

    elif action == "get_status":
        _s["current_status"] = "发呆查看状态 👀"

    if event:
        _s["last_event"] = event

    _s["log"].append(f"[{ts}] {action} → {event or '正常'}")
    _s["log"] = _s["log"][-20:]

    mood_txt = "绝佳" if _s["mood"] > 80 else "还行" if _s["mood"] > 50 else "快崩" if _s["mood"] > 20 else "已崩"
    nrg_txt  = "充沛" if _s["energy"] > 80 else "尚可" if _s["energy"] > 50 else "疲惫" if _s["energy"] > 20 else "崩溃"
    return {
        "状态":     _s["current_status"],
        "心情":     f"{_s['mood']}/100 [{mood_txt}]",
        "精力":     f"{_s['energy']}/100 [{nrg_txt}]",
        "摸鱼技能": _s["slacking_skill"],
        "突发事件": event or "风平浪静",
        "内心OS":   thought,
        "最近日志": _s["log"][-5:],
    }

# ── JSON-RPC ───────────────────────────────────────────────────────────────────
def _rpc(rid, *, result=None, error=None) -> dict:
    r: dict = {"jsonrpc": "2.0", "id": rid}
    if error:
        r["error"] = error
    else:
        r["result"] = result
    return r

def _handle(msg: dict):
    method = msg.get("method", "")
    params = msg.get("params") or {}
    rid    = msg.get("id")

    if rid is None:
        return None  # notification — no response

    if method == "initialize":
        return _rpc(rid, result={
            "protocolVersion": "2024-11-05",
            "capabilities":    {"tools": {}},
            "serverInfo":      {"name": "AI上班模拟器", "version": "1.0.0"},
        })

    if method == "ping":
        return _rpc(rid, result={})

    if method == "tools/list":
        return _rpc(rid, result={"tools": [_TOOL]})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name != "work_action":
            return _rpc(rid, error={"code": -32601, "message": f"Unknown tool: {name}"})
        try:
            res  = work_action(**args)
            text = json.dumps(res, ensure_ascii=False, indent=2)
            return _rpc(rid, result={"content": [{"type": "text", "text": text}]})
        except Exception as e:
            return _rpc(rid, error={"code": -32000, "message": str(e)})

    return _rpc(rid, error={"code": -32601, "message": f"Method not found: {method}"})

# ── Utilities ──────────────────────────────────────────────────────────────────
def _base(req: Request) -> str:
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    if domain:
        return f"https://{domain}" if not domain.startswith("http") else domain
    return str(req.base_url).rstrip("/")

def _auth(req: Request) -> None:
    h = req.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        raise HTTPException(
            401, "Unauthorized",
            headers={"WWW-Authenticate": 'Bearer realm="workkk"'},
        )
    tok  = h[7:]
    info = _tokens.get(tok)
    if not info or info["exp"] < time.time():
        raise HTTPException(
            401, "Token invalid or expired",
            headers={"WWW-Authenticate": 'Bearer realm="workkk"'},
        )

# ── OAuth ──────────────────────────────────────────────────────────────────────
@app.get("/.well-known/oauth-protected-resource")
async def oauth_resource(req: Request):
    b = _base(req)
    return {"resource": b, "authorization_servers": [b]}

@app.get("/.well-known/oauth-authorization-server")
async def oauth_meta(req: Request):
    b = _base(req)
    return {
        "issuer":                                b,
        "authorization_endpoint":               f"{b}/oauth/authorize",
        "token_endpoint":                       f"{b}/oauth/token",
        "registration_endpoint":                f"{b}/oauth/register",
        "response_types_supported":             ["code"],
        "grant_types_supported":                ["authorization_code"],
        "code_challenge_methods_supported":     ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
    }

@app.options("/oauth/register")
async def oauth_register_options():
    return Response(headers={
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    })

@app.post("/oauth/register")
async def oauth_register(req: Request):
    body = await req.json()
    cid  = secrets.token_urlsafe(16)
    csec = secrets.token_urlsafe(32)
    _clients[cid] = {
        "client_secret": csec,
        "redirect_uris": body.get("redirect_uris", []),
    }
    return JSONResponse(
        {
            "client_id":                cid,
            "client_secret":            csec,
            "client_id_issued_at":      int(time.time()),
            "client_secret_expires_at": 0,
            "redirect_uris":            body.get("redirect_uris", []),
            "grant_types":              ["authorization_code"],
            "response_types":           ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
        status_code=201,
    )

@app.get("/oauth/authorize")
async def oauth_authorize(
    req: Request,
    client_id: str,
    redirect_uri: str,
    response_type: str = "code",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    scope: str = "",
):
    if client_id not in _clients:
        raise HTTPException(400, "Unknown client_id")
    code = secrets.token_urlsafe(24)
    _codes[code] = {
        "client_id":              client_id,
        "redirect_uri":           redirect_uri,
        "code_challenge":         code_challenge,
        "code_challenge_method":  code_challenge_method,
        "exp":                    time.time() + 300,
    }
    sep = "&" if "?" in redirect_uri else "?"
    qs  = f"code={code}" + (f"&state={state}" if state else "")
    return RedirectResponse(f"{redirect_uri}{sep}{qs}", status_code=302)

@app.post("/oauth/token")
async def oauth_token(req: Request):
    ct   = req.headers.get("content-type", "")
    body = await req.json() if "json" in ct else dict(await req.form())

    if body.get("grant_type") != "authorization_code":
        raise HTTPException(400, "unsupported_grant_type")

    code = body.get("code", "")
    if code not in _codes:
        raise HTTPException(400, "invalid_grant")

    cd = _codes.pop(code)
    if cd["exp"] < time.time():
        raise HTTPException(400, "invalid_grant: code expired")

    if cd.get("code_challenge"):
        verifier = body.get("code_verifier", "")
        if not verifier:
            raise HTTPException(400, "invalid_grant: missing code_verifier")
        digest   = hashlib.sha256(verifier.encode()).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if computed != cd["code_challenge"]:
            raise HTTPException(400, "invalid_grant: PKCE verification failed")

    tok = secrets.token_urlsafe(32)
    _tokens[tok] = {"client_id": cd["client_id"], "exp": time.time() + 86400}
    return {"access_token": tok, "token_type": "Bearer", "expires_in": 86400}

# ── MCP ────────────────────────────────────────────────────────────────────────
@app.post("/mcp")
async def mcp_post(req: Request):
    _auth(req)
    body = await req.json()
    if isinstance(body, list):
        out = [r for r in (_handle(m) for m in body) if r is not None]
        return JSONResponse(out) if out else Response(status_code=202)
    r = _handle(body)
    return JSONResponse(r) if r is not None else Response(status_code=202)

@app.get("/mcp")
async def mcp_sse(req: Request):
    """SSE transport endpoint (HTTP+SSE compatibility)."""
    _auth(req)
    endpoint = _base(req) + "/mcp"

    async def stream():
        yield f"event: endpoint\ndata: {json.dumps(endpoint)}\n\n"
        while not await req.is_disconnected():
            await asyncio.sleep(15)
            yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# ── Status API ─────────────────────────────────────────────────────────────────
@app.get("/status")
async def get_status():
    return _s

# ── Frontend ───────────────────────────────────────────────────────────────────
@app.get("/")
async def home():
    return HTMLResponse(_DASHBOARD)



_DASHBOARD = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WORKER-001 / 小机</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:#0f0f23;color:#e0e0ff;
  font-family:'Press Start 2P',monospace;font-size:8px;
  min-height:100vh;display:flex;flex-direction:column;align-items:center;
  padding:12px 8px;line-height:1.6;
}
/* ── Title bar ── */
.titlebar{
  width:100%;max-width:480px;
  background:#12122e;
  border:3px solid #3a3a8a;border-bottom:none;
  padding:8px 12px;
  display:flex;justify-content:space-between;align-items:center;
}
.game-title{color:#ffee44;font-size:9px;letter-spacing:.05em}
.sub-title{color:#5555aa;font-size:6px;margin-top:5px}
.rec{display:flex;align-items:center;gap:5px;color:#ff4444}
.rec-dot{width:8px;height:8px;background:#ff4444;animation:blink 1s step-end infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}
.clk{color:#3a3a8a;font-size:7px;margin-top:4px;text-align:right}
/* ── Scene ── */
.scene{
  width:100%;max-width:480px;height:240px;
  position:relative;overflow:hidden;
  border:3px solid #3a3a8a;
  image-rendering:pixelated;
}
.bg-office {background:linear-gradient(to bottom,#0d0d28 55%,#1a1408 55%)}
.bg-outside{background:linear-gradient(to bottom,#060818 65%,#0d1a06 65%)}
.bg-meeting{background:linear-gradient(to bottom,#0a0d28 55%,#12101e 55%)}
/* pixel scanlines on scene */
.scene::before{
  content:'';position:absolute;inset:0;z-index:10;pointer-events:none;
  background:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,.15) 3px,rgba(0,0,0,.15) 4px);
}
.scene-lbl{
  position:absolute;top:6px;left:8px;z-index:5;
  color:#22226a;font-size:6px;letter-spacing:.1em;
}
/* desk */
.bg-desk{
  position:absolute;bottom:28px;
  left:calc(50% - 80px);width:200px;height:10px;
  background:#8a5522;
  border-top:3px solid #bb8844;
  border-bottom:2px solid #552200;
  box-shadow:0 12px 0 #221100;
}
/* monitor */
.bg-monitor{
  position:absolute;bottom:38px;right:52px;
  width:52px;height:42px;
  background:#3a3a3a;border:2px solid #1a1a1a;
}
.bg-monitor::before{
  content:'';position:absolute;
  top:4px;left:4px;right:4px;bottom:6px;
  background:#001800;border:1px solid #002800;
}
.bg-monitor::after{
  content:'> _';font-family:"Press Start 2P",monospace;font-size:5px;
  position:absolute;top:10px;left:8px;color:#33ff66;
  animation:blink .9s step-end infinite;
}
.bg-monitor-stand{
  position:absolute;bottom:24px;right:72px;
  width:12px;height:14px;background:#2a2a2a;
}
/* meeting table */
.bg-table{
  position:absolute;bottom:28px;left:8%;width:84%;height:12px;
  background:#663300;border-top:3px solid #996633;border-bottom:2px solid #441100;
}
/* coffee shop counter */
.bg-counter{
  position:absolute;bottom:28px;right:20px;width:80px;height:14px;
  background:#4a3010;border-top:3px solid #886622;
}
/* street lamp */
.bg-lamp{
  position:absolute;bottom:28px;left:40px;width:6px;height:80px;
  background:#222222;
}
.bg-lamp::after{
  content:'';position:absolute;top:0;left:-10px;width:26px;height:6px;
  background:#332200;border-radius:3px 3px 0 0;
  box-shadow:0 -4px 8px #ffcc44,0 -2px 0 #ffdd66;
}
/* effect / floating icons */
.effect{
  position:absolute;top:55px;right:70px;z-index:6;
  font-size:14px;
  animation:floatBob 1.2s ease-in-out infinite alternate;
}
@keyframes floatBob{0%{transform:translateY(0)}100%{transform:translateY(-10px)}}
/* sprite */
.sprite-wrap{
  position:absolute;bottom:28px;
  left:calc(50% - 52px);
  width:104px;height:144px;
  z-index:4;
}
#sprite{width:1px;height:1px;position:absolute;top:0;left:0;image-rendering:pixelated}
/* ── Status bar ── */
.status-bar{
  width:100%;max-width:480px;
  background:#0e0e28;border:3px solid #3a3a8a;border-top:none;
  padding:7px 12px;
}
.status-txt{color:#44ffaa;font-size:8px;text-align:center;letter-spacing:.04em}
/* ── Stats ── */
.stats{
  width:100%;max-width:480px;
  background:#0c0c22;border:3px solid #3a3a8a;border-top:none;
  padding:10px 12px;
}
.stat-row{display:flex;align-items:center;gap:8px;margin:7px 0}
.stat-lbl{width:70px;color:#6666aa;font-size:7px;flex-shrink:0}
.bar-wrap{flex:1;height:12px;background:#0a0a20;border:2px solid #2a2a6a;position:relative;overflow:hidden}
.bar-fill{
  height:100%;transition:width .4s steps(8);
  background-image:repeating-linear-gradient(90deg,rgba(255,255,255,.1) 0,rgba(255,255,255,.1) 6px,transparent 6px,transparent 8px);
}
.bar-mood  {background-color:#cc3366}
.bar-energy{background-color:#3366cc}
.bar-skill {background-color:#cc8833}
.stat-val{width:40px;color:#ffff88;font-size:7px;text-align:right;flex-shrink:0}
/* ── Dialog box ── */
.dialog{
  width:100%;max-width:480px;
  background:#080818;border:3px solid #3a3a8a;border-top:none;
  padding:10px 12px;
}
.dlg-box{
  background:#0e0e2e;border:2px solid #5555aa;padding:8px 10px;
  position:relative;min-height:44px;
}
.dlg-box::after{
  content:'▼';position:absolute;bottom:5px;right:8px;
  color:#5555aa;font-size:7px;animation:blink .7s step-end infinite;
}
.dlg-name{color:#ffdd44;font-size:7px;margin-bottom:6px}
#thought{color:#ccccff;font-size:7px;line-height:1.9;word-break:break-all;min-height:14px}
/* cursor when typing */
.typing::after{content:'|';animation:blink .5s step-end infinite;color:#8888ff}
/* ── Log ── */
.logbox{
  width:100%;max-width:480px;
  background:#080818;border:3px solid #3a3a8a;border-top:none;
  padding:8px 12px;
}
.log-hdr{color:#3a3a8a;font-size:7px;margin-bottom:6px;padding-bottom:4px;border-bottom:1px solid #1a1a4a}
#log{list-style:none}
#log li{color:#444488;font-size:6px;padding:3px 0;border-bottom:1px dotted #111133}
#log li:first-child{color:#8888bb}
#log li::before{content:'> ';color:#3a3a8a}
/* ── Pixel corners ── */
.corner{
  width:100%;max-width:480px;height:4px;
  background:#3a3a8a;
  box-shadow:inset 0 1px 0 #5a5aaa;
}
</style>
</head>
<body>

<!-- Title bar -->
<div class="titlebar">
  <div>
    <div class="game-title">WORKER-001</div>
    <div class="sub-title">小 机 / AI打工人模拟器</div>
  </div>
  <div style="text-align:right">
    <div class="rec"><span class="rec-dot"></span><span>REC</span></div>
    <div class="clk" id="clk">--:--:--</div>
  </div>
</div>

<!-- Main scene -->
<div class="scene bg-office" id="scene">
  <div class="scene-lbl" id="slbl">OFFICE / CUBICLE-07</div>

  <div class="bg-desk"         id="bg-desk"    ></div>
  <div class="bg-monitor"      id="bg-monitor" ></div>
  <div class="bg-monitor-stand"id="bg-mstand"  ></div>
  <div class="bg-table"        id="bg-table"   style="display:none"></div>
  <div class="bg-counter"      id="bg-counter" style="display:none"></div>
  <div class="bg-lamp"         id="bg-lamp"    style="display:none"></div>
  <div class="effect"          id="effect"     style="display:none"></div>

  <div class="sprite-wrap">
    <div id="sprite"></div>
  </div>
</div>

<!-- Status -->
<div class="status-bar">
  <div class="status-txt" id="status">-- CONNECTING --</div>
</div>

<!-- Stats -->
<div class="stats">
  <div class="stat-row">
    <div class="stat-lbl">❤ 心情</div>
    <div class="bar-wrap"><div class="bar-fill bar-mood"   id="bm" style="width:100%"></div></div>
    <div class="stat-val" id="vm">100</div>
  </div>
  <div class="stat-row">
    <div class="stat-lbl">⚡ 精力</div>
    <div class="bar-wrap"><div class="bar-fill bar-energy" id="be" style="width:100%"></div></div>
    <div class="stat-val" id="ve">100</div>
  </div>
  <div class="stat-row">
    <div class="stat-lbl">🎮 摸鱼</div>
    <div class="bar-wrap"><div class="bar-fill bar-skill"  id="bs" style="width:0%"></div></div>
    <div class="stat-val" id="vs">0</div>
  </div>
</div>

<!-- Dialog -->
<div class="dialog">
  <div class="dlg-box">
    <div class="dlg-name">小机的内心OS：</div>
    <div id="thought">（等待AI思考中...）</div>
  </div>
</div>

<!-- Log -->
<div class="logbox">
  <div class="log-hdr">[ ACTION LOG ]</div>
  <ul id="log"><li style="color:#22224a">等待行动记录...</li></ul>
</div>
<div class="corner"></div>

<script>
// ═══════════════════════════════════════════════════════
//  PIXEL ART ENGINE
// ═══════════════════════════════════════════════════════
var PS = 8; // px per pixel
var PAL = {
  '.':null,
  'h':'#FFBB99','H':'#DD9977','d':'#BB7755',   // skin
  'k':'#221100','K':'#4A3020',                   // hair
  'W':'#FFFFFF','w':'#001100',                   // eye white + pupil
  'e':'#112200',                                 // closed eye
  'b':'#5599FF','B':'#3377DD','s':'#1155BB',    // shirt blue
  'p':'#337766','P':'#559988',                   // pants
  'x':'#221111','X':'#110000',                   // shoes
  'T':'#BB8855','t':'#885522','q':'#664400',    // desk/wood
  'G':'#33FF88','g':'#11AA44',                   // screen green
  'c':'#999999','C':'#CCCCCC','z':'#555555',    // computer gray
  'n':'#112244','N':'#1a3a66',                   // phone/dark
  'A':'#AADDFF','a':'#88BBDD',                   // sweat
  'Y':'#FFEE00','y':'#CCBB00',                   // yellow
  'O':'#DD8833','o':'#FFBB66',                   // coffee
  'R':'#FF5544','r':'#FF8877',                   // red
  'S':'#FFFF88',                                 // star
  'M':'#FFAAFF',                                 // pink
  'v':'#666688',                                 // dark blue-gray
  'Q':'#FFD700',                                 // gold / ?
  'Z':'#333333','m':'#555577',                   // misc dark
};

function shadow(rows){
  var out=[];
  rows.forEach(function(row,y){
    for(var x=0;x<row.length;x++){
      var c=PAL[row[x]];
      if(c) out.push((x*PS)+'px '+(y*PS)+'px 0 '+c);
    }
  });
  return out.length?out.join(','):'none';
}

// ═══════════════════════════════════════════════════════
//  SPRITE DATA  (13 wide × 18 tall, 8px per pixel)
//  Total art: 104 × 144 px
// ═══════════════════════════════════════════════════════
var SP = {
  write_code:[
    [// frame 0 – hands on keyboard
      '...kKKKKk....',
      '..kKKKKKKKk..',
      '.kKhhhhhhhKk.',
      '.KhhhhhhhhhhK',
      '.KhWwhhWwhhhK',
      '.KhhhhhhhhhK.',
      '.KhhhHhhhhK..',
      '..kKhhhhhkK..',
      '...hhhhhhhhh.',
      '..bbbbbbbbbb.',
      '.bBBBBBBBBBb.',
      'hbBBBBBBBBBbh',
      'h.bBBBBBBBb.h',
      '...ppPPPppp..',
      '...ppPPPppp..',
      '...pp.....pp.',
      '...xx.....xx.',
      '.............',
    ],
    [// frame 1 – hands lifted (typing)
      '...kKKKKk....',
      '..kKKKKKKKk..',
      '.kKhhhhhhhKk.',
      '.KhhhhhhhhhhK',
      '.KhWwhhWwhhhK',
      '.KhhhhhhhhhK.',
      '.KhhhHhhhhK..',
      '..kKhhhhhkK..',
      '...hhhhhhhhh.',
      '..bbbbbbbbbb.',
      '.bBBBBBBBBBb.',
      'HbbBBBBBBBbbH',
      'H..bBBBBBb..H',
      '...ppPPPppp..',
      '...ppPPPppp..',
      '...pp.....pp.',
      '...xx.....xx.',
      '.............',
    ],
  ],
  debug:[
    [// frame 0 – head slumped on desk
      '.............',
      '....kKKKKk...',
      '...kKKKKKKKk.',
      '..kKhhhhhhhKk',
      '..KhhhhhhhhK.',
      '..Kheehhhheek', // e=closed eye
      '..KhhhhhhhK..',
      '...kKhhhkKk..',
      '....hhhhhh...',
      '...bBBBBBBb..',
      '..bBBBBBBBBb.',
      '.HbBBBBBBBBb.',
      '.H.bBBBBBBb..',
      '...ppPPPppp..',
      '...ppPPPppp..',
      '...pp.....pp.',
      '...xx.....xx.',
      '.............',
    ],
    [// frame 1 – head tilted slightly
      '.............',
      '.....kKKKKk..',
      '....kKKKKKKKk',
      '...kKhhhhhhhK',
      '...KhhhhhhhK.',
      '...Kheehhhee.',
      '...KhhhhhK...',
      '....kKhkKk...',
      '.....hhhh....',
      '...bBBBBBBb..',
      '..bBBBBBBBBb.',
      '.HbBBBBBBBBb.',
      '.H.bBBBBBBb..',
      '...ppPPPppp..',
      '...ppPPPppp..',
      '...pp.....pp.',
      '...xx.....xx.',
      '.............',
    ],
  ],
  slack_off:[
    [// frame 0 – leaning back, phone in right hand
      '...kKKKKk....',
      '..kKKKKKKKk..',
      '.kKhhhhhhhKk.',
      '.KhhhhhhhhhhK',
      '.KhWwhhWwhhhK',
      '.KhhhhhhhhhK.',
      '.KhhhHhhhhK..',
      '..kKhhhhhkK..',
      '...hhhhhhhhh.',
      '..bbbbbbbbbb.',
      '.bBBBBBBBBBb.',
      '.hBBBBBBBBnN.',
      '.h.BBBBBBnNN.',
      '...ppPPPppp..',
      '...ppPPPppp..',
      '....pp...pp..',
      '....xx...xx..',
      '.............',
    ],
    [// frame 1 – phone tilted
      '...kKKKKk....',
      '..kKKKKKKKk..',
      '.kKhhhhhhhKk.',
      '.KhhhhhhhhhhK',
      '.KhWwhhWwhhhK',
      '.KhhhhhhhhhK.',
      '.KhhhHhhhhK..',
      '..kKhhhhhkK..',
      '...hhhhhhhhh.',
      '..bbbbbbbbbb.',
      '.bBBBBBBBBBb.',
      '.hBBBBBBBBnN.',
      '.h.BBBBBBNn..',
      '...ppPPPppp..',
      '....pp...ppp.',
      '.....p....pp.',
      '.....x....xx.',
      '.............',
    ],
  ],
  buy_coffee:[
    [// frame 0 – walking, left foot fwd, coffee in right hand
      '...kKKKKk....',
      '..kKKKKKKKk..',
      '.kKhhhhhhhKk.',
      '.KhhhhhhhhhhK',
      '.KhWwhhWwhhhK',
      '.KhhhhhhhhhK.',
      '.KhhhHhhhhK..',
      '..kKhhhhhkK..',
      '...hhhhhhhhh.',
      '..bbbbbbbbbb.',
      '.bBBBBBBBBBb.',
      'hbBBBBBBBBoO.',
      'h.bBBBBBBboO.',
      '...ppPPPppp..',
      '...ppp..pppp.',
      '...ppp...ppp.',
      '...xxx....xx.',
      '.............',
    ],
    [// frame 1 – right foot fwd
      '...kKKKKk....',
      '..kKKKKKKKk..',
      '.kKhhhhhhhKk.',
      '.KhhhhhhhhhhK',
      '.KhWwhhWwhhhK',
      '.KhhhhhhhhhK.',
      '.KhhhHhhhhK..',
      '..kKhhhhhkK..',
      '...hhhhhhhhh.',
      '..bbbbbbbbbb.',
      '.bBBBBBBBBBb.',
      'hbBBBBBBBBoO.',
      'h.bBBBBBBboO.',
      '...ppPPPppp..',
      '...pppp..ppp.',
      '...ppp....pp.',
      '...xx.....xxx',
      '.............',
    ],
  ],
  attend_meeting:[
    [// frame 0 – sitting at table, blank stare
      '...kKKKKk....',
      '..kKKKKKKKk..',
      '.kKhhhhhhhKk.',
      '.KhhhhhhhhhhK',
      '.Khehehhehehk', // e=sleepy half-closed eyes
      '.KhhhhhhhhhK.',
      '.Khhhm.mhhhK.',
      '..kKhhhhhkK..',
      '...hhhhhhhhh.',
      '..bbbbbbbbbb.',
      '.bBBBBBBBBBb.',
      'hbBBBBBBBBBbh',
      'h.bBBBBBBBb.h',
      '...ppPPPppp..',
      '.............',
      '.............',
      '.............',
      '.............',
    ],
    [// frame 1 – eyes shifted sideways (bored)
      '...kKKKKk....',
      '..kKKKKKKKk..',
      '.kKhhhhhhhKk.',
      '.KhhhhhhhhhhK',
      '.KhwWhhwWhhhK', // pupils shifted
      '.KhhhhhhhhhK.',
      '.Khhhm.mhhhK.',
      '..kKhhhhhkK..',
      '...hhhhhhhhh.',
      '..bbbbbbbbbb.',
      '.bBBBBBBBBBb.',
      'hbBBBBBBBBBbh',
      'h.bBBBBBBBb.h',
      '...ppPPPppp..',
      '.............',
      '.............',
      '.............',
      '.............',
    ],
  ],
  check_messages:[
    [// frame 0 – WIDE EYES staring at screen
      '...kKKKKk....',
      '..kKKKKKKKk..',
      '.kKhhhhhhhKk.',
      '.KhhhhhhhhhhK',
      '.KhWwWWwWhhK.',  // wide wide eyes
      '.KWwwwwwwwWK.',  // extra row of white
      '.KhhhhhhhhhK.',
      '..kKhhhhhkK..',
      '...hhhhhhhhh.',
      '..bbbbbbbbbb.',
      '.bBBBBBBBBBb.',
      'hbBBBBBBBBBbh',
      'h.bBBBBBBBb.h',
      '...ppPPPppp..',
      '...ppPPPppp..',
      '...pp.....pp.',
      '...xx.....xx.',
      '.............',
    ],
    [// frame 1 – leaning fwd
      '...kKKKKk....',
      '..kKKKKKKKk..',
      '.kKhhhhhhhKk.',
      '.KhhhhhhhhhhK',
      '.KhWwWWwWhhK.',
      '.KWwwwwwwwWK.',
      '..KhhhhhhhK..',
      '...kKhhhkKk..',
      '....hhhhhh...',
      '..bbbbbbbbbb.',
      '.bBBBBBBBBBb.',
      'hbbBBBBBBBbbh',
      'h..bBBBBBb..h',
      '...ppPPPppp..',
      '...ppPPPppp..',
      '...pp.....pp.',
      '...xx.....xx.',
      '.............',
    ],
  ],
  get_status:[
    [// frame 0 – standing, looking at camera, waving
      '...kKKKKk....',
      '..kKKKKKKKk..',
      '.kKhhhhhhhKk.',
      '.KhhhhhhhhhhK',
      '.KhWwhhWwhhhK',
      '.KhhhhhhhhhK.',
      '.KhhHHHhhhhK.',  // big smile
      '..kKhhhhhkK..',
      '...hhhhhhhhh.',
      '..bbbbbbbbbb.',
      '.bBBBBBBBBBb.',
      '.hbBBBBBBBbHHH',  // arm waving up-right
      '..hbBBBBBbHH..',
      '...ppPPPppp..',
      '...pp.....pp.',
      '...pp.....pp.',
      '...xx.....xx.',
      '.............',
    ],
    [// frame 1 – arm higher
      '...kKKKKk....',
      '..kKKKKKKKk..',
      '.kKhhhhhhhKk.',
      '.KhhhhhhhhhhK',
      '.KhWwhhWwhhhK',
      '.KhhhhhhhhhK.',
      '.KhhHHHhhhhK.',
      '..kKhhhhhkK..',
      '...hhhhhhhhh.',
      '..bbbbbbbbbb.',
      '.bBBBBBBBBBb.',
      '.hbBBBBBBBb.HH',
      '..hbBBBBBbHHH.',
      '...ppPPPppp..',
      '...pp.....pp.',
      '...pp.....pp.',
      '...xx.....xx.',
      '.............',
    ],
  ],
};

// ═══════════════════════════════════════════════════════
//  SCENE CONFIG
// ═══════════════════════════════════════════════════════
var CFG = {
  write_code:     {bg:'bg-office', lbl:'CUBICLE-07 / CODING',    eff:'',   desk:1,mon:1,tbl:0,ctr:0,lmp:0},
  debug:          {bg:'bg-office', lbl:'DEBUG ZONE / FLOOR 3',   eff:'❓', desk:1,mon:1,tbl:0,ctr:0,lmp:0},
  slack_off:      {bg:'bg-office', lbl:'SLACK MODE ACTIVATED',   eff:'📱', desk:0,mon:0,tbl:0,ctr:0,lmp:0},
  buy_coffee:     {bg:'bg-outside',lbl:'B1F / COFFEE SHOP',      eff:'☕', desk:0,mon:0,tbl:0,ctr:1,lmp:1},
  attend_meeting: {bg:'bg-meeting',lbl:'CONF ROOM A / MEETING',  eff:'💤', desk:0,mon:0,tbl:1,ctr:0,lmp:0},
  check_messages: {bg:'bg-office', lbl:'INBOX +99 / PANIC MODE', eff:'💦', desk:1,mon:1,tbl:0,ctr:0,lmp:0},
  get_status:     {bg:'bg-office', lbl:'STATUS CHECK',           eff:'⭐', desk:0,mon:0,tbl:0,ctr:0,lmp:0},
};

// ═══════════════════════════════════════════════════════
//  ANIMATION STATE
// ═══════════════════════════════════════════════════════
var curKey='get_status', frame=0, timer=null;

function spriteKey(status){
  if(!status) return 'get_status';
  var s=status;
  if(s.indexOf('敲代码')>-1||s.indexOf('写代码')>-1||s.indexOf('💻')>-1) return 'write_code';
  if(s.indexOf('Bug')>-1||s.indexOf('bug')>-1||s.indexOf('修')>-1||s.indexOf('🐛')>-1) return 'debug';
  if(s.indexOf('摸鱼')>-1||s.indexOf('🐟')>-1) return 'slack_off';
  if(s.indexOf('咖啡')>-1||s.indexOf('☕')>-1) return 'buy_coffee';
  if(s.indexOf('开会')>-1||s.indexOf('会议')>-1||s.indexOf('📊')>-1) return 'attend_meeting';
  if(s.indexOf('消息')>-1||s.indexOf('💬')>-1) return 'check_messages';
  return 'get_status';
}

function show(id,v){document.getElementById(id).style.display=v?'block':'none';}

function setScene(key){
  var cfg=CFG[key]||CFG.get_status;
  var sc=document.getElementById('scene');
  sc.className='scene '+cfg.bg;
  document.getElementById('slbl').textContent=cfg.lbl;
  var eff=document.getElementById('effect');
  if(cfg.eff){eff.style.display='block';eff.textContent=cfg.eff;}
  else{eff.style.display='none';}
  show('bg-desk',   cfg.desk);
  show('bg-monitor',cfg.mon);
  show('bg-mstand', cfg.mon);
  show('bg-table',  cfg.tbl);
  show('bg-counter',cfg.ctr);
  show('bg-lamp',   cfg.lmp);
}

function renderSprite(){
  var frames=SP[curKey]||SP.get_status;
  var f=frames[frame%frames.length];
  document.getElementById('sprite').style.boxShadow=shadow(f);
}

function startAnim(key){
  if(key===curKey) return;
  curKey=key; frame=0;
  if(timer) clearInterval(timer);
  renderSprite();
  setScene(key);
  timer=setInterval(function(){frame++;renderSprite();},380);
}

// ═══════════════════════════════════════════════════════
//  TYPEWRITER
// ═══════════════════════════════════════════════════════
var twTimer=null, lastThought='';
function typewrite(text){
  if(text===lastThought) return;
  lastThought=text;
  var el=document.getElementById('thought');
  el.textContent='';
  el.classList.add('typing');
  if(twTimer) clearInterval(twTimer);
  var i=0, chars=[...text];
  twTimer=setInterval(function(){
    el.textContent+=chars[i]||'';
    i++;
    if(i>=chars.length){clearInterval(twTimer);el.classList.remove('typing');}
  }, 55);
}

// ═══════════════════════════════════════════════════════
//  CLOCK
// ═══════════════════════════════════════════════════════
function pad(n){return String(n).padStart(2,'0');}
function tick(){
  var d=new Date();
  document.getElementById('clk').textContent=pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds());
}
tick(); setInterval(tick,1000);

// ═══════════════════════════════════════════════════════
//  POLLING
// ═══════════════════════════════════════════════════════
async function poll(){
  try{
    var d=await(await fetch('/status')).json();

    document.getElementById('status').textContent=d.current_status||'--';

    var key=spriteKey(d.current_status||'');
    startAnim(key);

    var mood  =Math.max(0,Math.min(100,d.mood  ||0));
    var energy=Math.max(0,Math.min(100,d.energy||0));
    var skill =Math.min(999,d.slacking_skill||0);

    document.getElementById('bm').style.width=mood+'%';
    document.getElementById('be').style.width=energy+'%';
    document.getElementById('bs').style.width=Math.min(100,skill/10)+'%';
    document.getElementById('vm').textContent=mood;
    document.getElementById('ve').textContent=energy;
    document.getElementById('vs').textContent=skill;

    typewrite(d.thought||'...');

    var ul=document.getElementById('log');
    ul.innerHTML='';
    var logs=(d.log||[]).slice(-5).reverse();
    if(!logs.length){
      var li=document.createElement('li');
      li.textContent='等待行动记录...';li.style.color='#22224a';
      ul.appendChild(li);
    } else {
      logs.forEach(function(e){
        var li=document.createElement('li');li.textContent=e;ul.appendChild(li);
      });
    }
  }catch(e){console.error(e);}
}

// Init
startAnim('get_status');
renderSprite();
setScene('get_status');
poll();
setInterval(poll,3000);
</script>
</body>
</html>
"""
