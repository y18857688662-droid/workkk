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
<title>AI打工人监控系统</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --g:#00ff41;--c:#00d4ff;--r:#ff3333;--y:#ffcc00;
  --bg:#070707;--panel:#0c0c0c;
}
body{
  background:var(--bg);color:var(--g);
  font-family:'Courier New',monospace;
  min-height:100vh;padding:14px;overflow-x:hidden;
}
/* CRT scanline overlay */
body::before{
  content:'';position:fixed;inset:0;
  background:repeating-linear-gradient(
    0deg,transparent,transparent 2px,
    rgba(0,0,0,.07) 2px,rgba(0,0,0,.07) 4px
  );
  pointer-events:none;z-index:9999;
}
/* Vignette */
body::after{
  content:'';position:fixed;inset:0;
  background:radial-gradient(ellipse at center,transparent 60%,rgba(0,0,0,.6) 100%);
  pointer-events:none;z-index:9998;
}
.hdr{
  display:flex;justify-content:space-between;align-items:center;
  border-bottom:1px solid var(--g);padding-bottom:8px;margin-bottom:14px;
}
.hdr-title{font-size:1.1rem;letter-spacing:.2em;text-transform:uppercase}
.blink{animation:blink 1s step-end infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.panel{
  background:var(--panel);border:1px solid #1c1c1c;
  padding:14px 16px;position:relative;
}
.panel::before{
  content:attr(data-label);
  position:absolute;top:-8px;left:12px;
  background:var(--panel);padding:0 6px;
  font-size:.58rem;color:var(--c);letter-spacing:.14em;
}
/* corner marks */
.panel::after{
  content:'';position:absolute;bottom:5px;right:5px;
  width:10px;height:10px;
  border-bottom:1px solid #2a2a2a;border-right:1px solid #2a2a2a;
}
.big{
  font-size:1.9rem;color:var(--c);text-align:center;
  padding:12px 0;min-height:64px;
  display:flex;align-items:center;justify-content:center;
  text-shadow:0 0 14px rgba(0,212,255,.45);
  word-break:break-all;line-height:1.2;
}
.bar-row{margin:9px 0}
.bar-lbl{
  display:flex;justify-content:space-between;
  font-size:.68rem;color:#555;margin-bottom:4px;
}
.bar-track{height:13px;background:#0e0e0e;border:1px solid #1c1c1c;overflow:hidden}
.bar-fill{height:100%;transition:width .6s ease,background .6s ease}
.thought{
  font-size:.85rem;color:#999;font-style:italic;
  padding:9px 12px;border-left:2px solid var(--y);
  min-height:42px;word-break:break-all;line-height:1.5;
}
.thought::before{content:'\201C';color:var(--y);font-style:normal}
.thought::after{content:'\201D';color:var(--y);font-style:normal}
.event{
  font-size:.78rem;padding:7px 10px;margin-top:10px;
  color:var(--r);border:1px solid #2b0000;background:#100000;
  min-height:32px;word-break:break-all;line-height:1.4;
}
.log-wrap{grid-column:1/-1}
.log{list-style:none;max-height:170px;overflow-y:auto}
.log li{font-size:.68rem;color:#3d3d3d;padding:4px 0;border-bottom:1px solid #111}
.log li:first-child{color:#6a6a6a}
.stat-row{margin-top:12px;display:flex;gap:24px}
.sv{font-size:1.4rem;color:var(--y)}
.sl{font-size:.58rem;color:#444;letter-spacing:.1em;margin-top:2px}
.rec{display:inline-flex;align-items:center;gap:5px;font-size:.62rem;color:var(--r)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--r)}
.ts{font-size:.62rem;color:#2a2a2a}
.cam{position:fixed;font-size:.52rem;color:#161616;letter-spacing:.05em}
.cam.tl{top:7px;left:7px}.cam.tr{top:7px;right:7px}
.cam.bl{bottom:7px;left:7px}.cam.br{bottom:7px;right:7px}
@media(max-width:560px){
  .grid{grid-template-columns:1fr}
  .log-wrap{grid-column:1}
  .big{font-size:1.35rem}
}
</style>
</head>
<body>
<div class="cam tl">CH-01 / AI-WORKER-001</div>
<div class="cam tr">4K·30FPS / IR-ON</div>
<div class="cam bl">MOTION-DETECT: ACTIVE</div>
<div class="cam br">UPTIME: <span id="uptime">00:00:00</span></div>

<div class="hdr">
  <span class="hdr-title">[ AI打工人实时监控系统 v2.7 ]</span>
  <span style="display:flex;gap:14px;align-items:center">
    <span class="rec"><span class="dot blink"></span>REC</span>
    <span class="ts" id="clk">--:--:--</span>
  </span>
</div>

<div class="grid">

  <!-- Current status - full width -->
  <div class="panel" data-label="CURRENT STATUS" style="grid-column:1/-1">
    <div class="big" id="status">正在连接监控信号...</div>
  </div>

  <!-- Vitals -->
  <div class="panel" data-label="VITAL SIGNS">
    <div class="bar-row">
      <div class="bar-lbl"><span>心情 MOOD</span><span id="mv">--</span></div>
      <div class="bar-track"><div class="bar-fill" id="mb" style="width:100%;background:#00ff41"></div></div>
    </div>
    <div class="bar-row">
      <div class="bar-lbl"><span>精力 ENERGY</span><span id="ev">--</span></div>
      <div class="bar-track"><div class="bar-fill" id="eb" style="width:100%;background:#00d4ff"></div></div>
    </div>
    <div class="stat-row">
      <div><div class="sv" id="sk">0</div><div class="sl">摸鱼技能</div></div>
    </div>
  </div>

  <!-- Inner thoughts -->
  <div class="panel" data-label="INNER THOUGHTS / LAST EVENT">
    <div class="thought" id="thought">等待AI打工人思考中...</div>
    <div class="event" id="event">暂无异常事件</div>
  </div>

  <!-- Log -->
  <div class="panel log-wrap" data-label="ACTION LOG">
    <ul class="log" id="log">
      <li style="color:#2a2a2a">等待行动记录...</li>
    </ul>
  </div>

</div>

<script>
var _start = Date.now();

function clr(v) {
  if (v > 80) return '#00ff41';
  if (v > 50) return '#ffd93d';
  if (v > 20) return '#ff9900';
  return '#ff3333';
}

function pad(n) { return String(n).padStart(2,'0'); }

function tick() {
  var now = new Date();
  document.getElementById('clk').textContent =
    pad(now.getHours())+':'+pad(now.getMinutes())+':'+pad(now.getSeconds());

  var s = Math.floor((Date.now()-_start)/1000);
  var h = Math.floor(s/3600), m = Math.floor((s%3600)/60), ss = s%60;
  document.getElementById('uptime').textContent = pad(h)+':'+pad(m)+':'+pad(ss);
}
tick(); setInterval(tick, 1000);

async function poll() {
  try {
    var d = await (await fetch('/status')).json();
    document.getElementById('status').textContent  = d.current_status || '--';
    document.getElementById('thought').textContent = d.thought        || '...';
    document.getElementById('event').textContent   = d.last_event     || '暂无';
    document.getElementById('mv').textContent = d.mood   + '/100';
    document.getElementById('ev').textContent = d.energy + '/100';
    document.getElementById('mb').style.cssText = 'width:'+d.mood  +'%;background:'+clr(d.mood);
    document.getElementById('eb').style.cssText = 'width:'+d.energy+'%;background:'+clr(d.energy);
    document.getElementById('sk').textContent = d.slacking_skill;

    var ul = document.getElementById('log');
    ul.innerHTML = '';
    var logs = (d.log || []).slice().reverse();
    logs.forEach(function(e) {
      var li = document.createElement('li');
      li.textContent = e;
      ul.appendChild(li);
    });
  } catch(e) { console.error(e); }
}

poll(); setInterval(poll, 2000);
</script>
</body>
</html>
"""
