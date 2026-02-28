from flask import Flask, render_template_string, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime
import sqlite3
import hashlib
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-in-production')
socketio = SocketIO(app, cors_allowed_origins="*")

# Active connections: {sid: {name, color, room}}
users = {}

DB_PATH = "chat_history.db"

# ── Helpers ───────────────────────────────────────────────────────────────────

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ── Database setup ────────────────────────────────────────────────────────────

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                password   TEXT    NOT NULL,
                color      TEXT    NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                room       TEXT    NOT NULL,
                name       TEXT    NOT NULL,
                color      TEXT    NOT NULL,
                text       TEXT    NOT NULL,
                time       TEXT    NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

# ── User account functions ────────────────────────────────────────────────────

COLORS = [
    "#e74c3c","#e67e22","#f1c40f","#2ecc71",
    "#1abc9c","#3498db","#9b59b6","#e91e63",
    "#00bcd4","#ff5722","#8bc34a","#ff9800",
    "#ff6b6b","#48dbfb","#ff9ff3","#54a0ff",
]

def next_color():
    with get_db() as conn:
        taken = {r["color"] for r in conn.execute("SELECT color FROM users").fetchall()}
        count = conn.execute("SELECT COUNT(*) as n FROM users").fetchone()["n"]
    for c in COLORS:
        if c not in taken:
            return c
    return COLORS[count % len(COLORS)]

def register_user(name, password):
    color = next_color()
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (name, password, color) VALUES (?, ?, ?)",
                (name, hash_password(password), color)
            )
            conn.commit()
        return True, color
    except sqlite3.IntegrityError:
        return False, "Username already taken."

def login_user(name, password):
    with get_db() as conn:
        row = conn.execute(
            "SELECT color, password FROM users WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
    if not row:
        return False, "Username not found."
    if row["password"] != hash_password(password):
        return False, "Wrong password."
    return True, row["color"]

# ── Message functions ─────────────────────────────────────────────────────────

def save_message(room, name, color, text, time):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO messages (room, name, color, text, time) VALUES (?, ?, ?, ?, ?)",
            (room, name, color, text, time)
        )
        conn.commit()

def load_history(room, limit=100):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT name, color, text, time FROM messages WHERE room=? ORDER BY id DESC LIMIT ?",
            (room, limit)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]

init_db()

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LiveChat</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
<style>
  :root {
    --bg:#0d0d0d; --panel:#141414; --border:#252525;
    --accent:#c8f135; --text:#f0f0f0; --muted:#555; --msg-bg:#1a1a1a; --error:#ff6b6b;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  html,body{height:100%;background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;overflow:hidden;}

  /* Auth / Room screens */
  .screen{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:var(--bg);z-index:100;}
  .screen.hidden{display:none;}
  .box{width:420px;padding:48px;border:1px solid var(--border);background:var(--panel);position:relative;overflow:hidden;}
  .box::before{content:'';position:absolute;top:-60px;right:-60px;width:180px;height:180px;border-radius:50%;background:radial-gradient(circle,rgba(200,241,53,.15) 0%,transparent 70%);pointer-events:none;}
  .box h1{font-family:'Space Mono',monospace;font-size:1.8rem;color:var(--accent);letter-spacing:-1px;margin-bottom:6px;}
  .box p{color:var(--muted);font-size:.9rem;margin-bottom:28px;}

  .tabs{display:flex;margin-bottom:24px;border-bottom:1px solid var(--border);}
  .tab{padding:10px 20px;font-size:.82rem;letter-spacing:1px;text-transform:uppercase;cursor:pointer;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px;transition:color .2s,border-color .2s;}
  .tab.active{color:var(--accent);border-bottom-color:var(--accent);}

  .field{margin-bottom:16px;}
  .field label{display:block;font-size:.72rem;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);margin-bottom:7px;}
  .field input{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:11px 14px;font-size:.92rem;font-family:'DM Sans',sans-serif;outline:none;transition:border-color .2s;border-radius:2px;}
  .field input:focus{border-color:var(--accent);}

  .btn{width:100%;padding:13px;background:var(--accent);color:#0d0d0d;font-family:'Space Mono',monospace;font-size:.85rem;font-weight:700;border:none;cursor:pointer;letter-spacing:1px;text-transform:uppercase;transition:opacity .2s;margin-top:6px;border-radius:2px;}
  .btn:hover{opacity:.85;}
  .err-box{background:rgba(255,107,107,.1);border:1px solid rgba(255,107,107,.3);color:var(--error);padding:10px 14px;font-size:.85rem;border-radius:2px;margin-bottom:16px;display:none;}
  .note{font-size:.78rem;color:var(--muted);margin-top:14px;text-align:center;}

  /* App */
  #app{display:none;height:100vh;flex-direction:row;}
  #app.visible{display:flex;}

  .sidebar{width:240px;min-width:240px;background:var(--panel);border-right:1px solid var(--border);display:flex;flex-direction:column;}
  .sidebar-header{padding:20px 18px 16px;border-bottom:1px solid var(--border);}
  .sidebar-header h2{font-family:'Space Mono',monospace;font-size:1rem;color:var(--accent);}
  .sidebar-header span{font-size:.78rem;color:var(--muted);}
  .sidebar-me{padding:12px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:9px;font-size:.88rem;}
  .sidebar-me .dot{width:10px;height:10px;border-radius:50%;flex-shrink:0;}
  .sidebar-me .me-name{font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .logout-btn{font-size:.7rem;cursor:pointer;padding:3px 8px;border:1px solid var(--border);border-radius:2px;background:none;color:var(--muted);transition:color .2s,border-color .2s;}
  .logout-btn:hover{color:var(--error);border-color:var(--error);}
  .sidebar-section{padding:14px 18px 8px;}
  .sidebar-section h3{font-size:.65rem;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:10px;}
  .user-list{flex:1;overflow-y:auto;padding:0 10px 10px;}
  .user-item{display:flex;align-items:center;gap:9px;padding:8px;font-size:.88rem;animation:fadeIn .3s ease;}
  .user-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0;}
  .user-name{color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .user-name.me{font-weight:600;}

  .chat-area{flex:1;display:flex;flex-direction:column;overflow:hidden;}
  .chat-header{padding:16px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:14px;background:var(--panel);}
  .chat-header .room-name{font-family:'Space Mono',monospace;font-size:1rem;}
  .chat-header .room-name span{color:var(--accent);}
  .online-badge{font-size:.72rem;background:rgba(200,241,53,.12);color:var(--accent);padding:3px 10px;border-radius:20px;margin-left:auto;}

  .messages{flex:1;overflow-y:auto;padding:24px;display:flex;flex-direction:column;gap:4px;}
  .messages::-webkit-scrollbar{width:4px;}
  .messages::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}

  .msg{display:flex;flex-direction:column;max-width:72%;animation:msgIn .25s ease;}
  .msg.own{align-self:flex-end;align-items:flex-end;}
  .msg.other{align-self:flex-start;align-items:flex-start;}
  .msg.system{align-self:center;align-items:center;max-width:100%;}
  .msg.gap{margin-top:14px;}
  .msg-meta{display:flex;align-items:baseline;gap:8px;margin-bottom:4px;padding:0 4px;}
  .msg-author{font-size:.78rem;font-weight:600;}
  .msg-time{font-size:.68rem;color:var(--muted);}
  .msg-bubble{padding:10px 14px;font-size:.92rem;line-height:1.55;border-radius:2px;word-break:break-word;max-width:100%;}
  .msg.other .msg-bubble{background:var(--msg-bg);border-left:3px solid;}
  .msg.own .msg-bubble{background:rgba(200,241,53,.12);border-right:3px solid var(--accent);}
  .msg.system .msg-bubble{background:transparent;color:var(--muted);font-size:.78rem;font-style:italic;padding:4px 0;}

  @keyframes msgIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
  @keyframes fadeIn{from{opacity:0}to{opacity:1}}

  .input-bar{padding:16px 24px;border-top:1px solid var(--border);display:flex;gap:12px;align-items:flex-end;background:var(--panel);}
  #msg-input{flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:12px 16px;font-size:.92rem;font-family:'DM Sans',sans-serif;outline:none;resize:none;max-height:120px;line-height:1.5;transition:border-color .2s;border-radius:2px;}
  #msg-input:focus{border-color:var(--accent);}
  .send-btn{padding:12px 22px;background:var(--accent);color:#0d0d0d;font-family:'Space Mono',monospace;font-size:.8rem;font-weight:700;border:none;cursor:pointer;border-radius:2px;white-space:nowrap;transition:opacity .15s;}
  .send-btn:hover{opacity:.85;}
  .typing-indicator{padding:0 24px 8px;font-size:.75rem;color:var(--muted);min-height:22px;font-style:italic;}
</style>
</head>
<body>

<!-- Auth screen -->
<div id="auth-screen" class="screen">
  <div class="box">
    <h1>LiveChat</h1>
    <div class="tabs">
      <div class="tab active" onclick="switchTab('login')">Login</div>
      <div class="tab" onclick="switchTab('register')">Register</div>
    </div>
    <div id="auth-error" class="err-box"></div>
    <div class="field"><label>Username</label>
      <input id="auth-name" type="text" placeholder="e.g. Alice" maxlength="24" autocomplete="username" /></div>
    <div class="field"><label>Password</label>
      <input id="auth-pass" type="password" placeholder="••••••••" maxlength="64" autocomplete="current-password" /></div>
    <button class="btn" id="auth-btn" onclick="submitAuth()">Login →</button>
    <p class="note" id="auth-note">New here? Switch to Register to create an account.</p>
  </div>
</div>

<!-- Room screen -->
<div id="room-screen" class="screen hidden">
  <div class="box">
    <h1>Pick a Room</h1>
    <p>Enter a room name to join or create it.</p>
    <div class="field"><label>Room</label>
      <input id="room-input" type="text" placeholder="e.g. general" maxlength="32" value="general" /></div>
    <button class="btn" onclick="joinRoom()">Join Room →</button>
  </div>
</div>

<!-- App -->
<div id="app">
  <div class="sidebar">
    <div class="sidebar-header">
      <h2>LiveChat</h2>
      <span id="my-room-label"></span>
    </div>
    <div class="sidebar-me">
      <div class="dot" id="my-dot"></div>
      <span class="me-name" id="my-name-label"></span>
      <button class="logout-btn" onclick="logout()">logout</button>
    </div>
    <div class="sidebar-section"><h3>Online</h3></div>
    <div class="user-list" id="user-list"></div>
  </div>
  <div class="chat-area">
    <div class="chat-header">
      <div class="room-name"># <span id="room-label"></span></div>
      <div class="online-badge" id="online-count">1 online</div>
    </div>
    <div class="messages" id="messages"></div>
    <div class="typing-indicator" id="typing-indicator"></div>
    <div class="input-bar">
      <textarea id="msg-input" rows="1" placeholder="Message…"></textarea>
      <button class="send-btn" onclick="sendMessage()">Send</button>
    </div>
  </div>
</div>

<script>
  const socket = io();
  let myName='', myColor='', currentTab='login', lastSender=null;
  let typingTimeout=null, isTyping=false;

  // ── Tab switch ──
  function switchTab(tab) {
    currentTab = tab;
    document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active',(i===0&&tab==='login')||(i===1&&tab==='register')));
    document.getElementById('auth-btn').textContent = tab==='login' ? 'Login →' : 'Register →';
    document.getElementById('auth-note').textContent = tab==='login'
      ? 'New here? Switch to Register to create an account.'
      : 'Already have an account? Switch to Login.';
    document.getElementById('auth-error').style.display='none';
    document.getElementById('auth-pass').value='';
  }

  function showError(msg) {
    const el=document.getElementById('auth-error');
    el.textContent=msg; el.style.display='block';
  }

  // ── Auth submit ──
  function submitAuth() {
    const name=document.getElementById('auth-name').value.trim();
    const pass=document.getElementById('auth-pass').value;
    if (!name) { showError('Please enter a username.'); return; }
    if (!pass)  { showError('Please enter a password.'); return; }
    if (pass.length < 4) { showError('Password must be at least 4 characters.'); return; }
    socket.emit('auth', { action: currentTab, name, password: pass });
  }

  socket.on('auth_result', data => {
    if (!data.ok) { showError(data.error); return; }
    myName=data.name; myColor=data.color;
    document.getElementById('auth-screen').classList.add('hidden');
    document.getElementById('room-screen').classList.remove('hidden');
    document.getElementById('room-input').focus();
  });

  // ── Join room ──
  function joinRoom() {
    const room = document.getElementById('room-input').value.trim() || 'general';
    socket.emit('join', { room });
    document.getElementById('room-screen').classList.add('hidden');
    document.getElementById('app').classList.add('visible');
    document.getElementById('room-label').textContent = room;
    document.getElementById('my-room-label').textContent = '#'+room;
    document.getElementById('my-name-label').textContent = myName;
    document.getElementById('my-dot').style.background = myColor;
    document.getElementById('msg-input').focus();
  }

  function logout() { location.reload(); }

  // ── Send ──
  function sendMessage() {
    const input=document.getElementById('msg-input');
    const text=input.value.trim();
    if (!text) return;
    socket.emit('message', { text });
    input.value=''; input.style.height='auto';
    clearTyping();
  }

  // ── Input events ──
  window.addEventListener('load', () => {
    document.getElementById('msg-input').addEventListener('keydown', e => {
      if (e.key==='Enter'&&!e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    document.getElementById('msg-input').addEventListener('input', () => {
      const ta=document.getElementById('msg-input');
      ta.style.height='auto'; ta.style.height=Math.min(ta.scrollHeight,120)+'px';
      if (!isTyping) { isTyping=true; socket.emit('typing',{typing:true}); }
      clearTimeout(typingTimeout);
      typingTimeout=setTimeout(clearTyping, 1500);
    });
    document.getElementById('auth-pass').addEventListener('keydown', e => { if(e.key==='Enter') submitAuth(); });
    document.getElementById('room-input').addEventListener('keydown', e => { if(e.key==='Enter') joinRoom(); });
  });

  function clearTyping() {
    if (isTyping) { isTyping=false; socket.emit('typing',{typing:false}); }
  }

  // ── History ──
  socket.on('history', messages => {
    messages.forEach(d => renderMsg(d, false));
    if (messages.length) {
      const div=document.createElement('div');
      div.className='msg system';
      div.innerHTML='<div class="msg-bubble">── you joined ──</div>';
      document.getElementById('messages').appendChild(div);
    }
    document.getElementById('messages').scrollTop=999999;
    lastSender=null;
  });

  // ── Live messages ──
  socket.on('message', d => renderMsg(d, true));

  function renderMsg(data, animate) {
    const msgs=document.getElementById('messages');
    const isOwn=data.name===myName, isSys=data.type==='system';
    const isGap=lastSender!==data.name;
    lastSender=isSys?null:data.name;
    const div=document.createElement('div');
    div.className='msg '+(isSys?'system':isOwn?'own':'other')+(isGap&&!isSys?' gap':'');
    if (!animate) div.style.animation='none';
    if (isSys) {
      div.innerHTML=`<div class="msg-bubble">${data.text}</div>`;
    } else {
      div.innerHTML=`
        ${isGap?`<div class="msg-meta">
          <span class="msg-author" style="color:${data.color}">${esc(data.name)}</span>
          <span class="msg-time">${data.time}</span></div>`:''}
        <div class="msg-bubble" ${!isOwn?`style="border-color:${data.color}"`:''}>${esc(data.text)}</div>`;
    }
    msgs.appendChild(div);
    msgs.scrollTop=msgs.scrollHeight;
  }

  // ── User list ──
  socket.on('users', data => {
    const list=document.getElementById('user-list');
    list.innerHTML='';
    data.forEach(u => {
      const el=document.createElement('div');
      el.className='user-item';
      const isMe=u.name===myName;
      el.innerHTML=`<div class="user-dot" style="background:${u.color}"></div>
        <div class="user-name ${isMe?'me':''}">${esc(u.name)}${isMe?' (you)':''}</div>`;
      list.appendChild(el);
    });
    document.getElementById('online-count').textContent=data.length+' online';
  });

  // ── Typing ──
  let typingUsers=new Set();
  socket.on('typing', data => {
    if (data.typing) typingUsers.add(data.name); else typingUsers.delete(data.name);
    const others=[...typingUsers].filter(n=>n!==myName);
    const el=document.getElementById('typing-indicator');
    if (!others.length) el.textContent='';
    else if (others.length===1) el.textContent=`${others[0]} is typing…`;
    else el.textContent=`${others.slice(0,-1).join(', ')} and ${others.at(-1)} are typing…`;
  });

  function esc(t) {
    return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
  }
</script>
</body>
</html>
"""

# ── Flask routes & SocketIO events ───────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/messages")
def api_messages():
    from flask import jsonify
    room  = request.args.get("room", "general")
    limit = min(int(request.args.get("limit", 5)), 20)
    msgs  = load_history(room, limit)
    return jsonify(msgs)

@socketio.on("auth")
def on_auth(data):
    action   = data.get("action", "")
    name     = data.get("name", "").strip()[:24]
    password = data.get("password", "")

    if not name or not password:
        emit("auth_result", {"ok": False, "error": "Username and password required."})
        return

    if action == "register":
        ok, result = register_user(name, password)
    elif action == "login":
        ok, result = login_user(name, password)
    else:
        emit("auth_result", {"ok": False, "error": "Invalid action."})
        return

    if ok:
        users[request.sid] = {"name": name, "color": result, "room": None}
        emit("auth_result", {"ok": True, "name": name, "color": result})
    else:
        emit("auth_result", {"ok": False, "error": result})

@socketio.on("join")
def on_join(data):
    user = users.get(request.sid)
    if not user:
        emit("message", {"type": "system", "text": "Please log in first.", "time": ""})
        return
    room = data.get("room", "general")[:32]
    user["room"] = room
    join_room(room)

    history = load_history(room)
    if history:
        emit("history", history)

    now = datetime.now().strftime("%H:%M")
    emit("message", {"type": "system", "text": f"{user['name']} joined", "time": now}, to=room)
    broadcast_users(room)

@socketio.on("message")
def on_message(data):
    user = users.get(request.sid)
    if not user or not user.get("room"): return
    now = datetime.now().strftime("%H:%M")
    msg = {"name": user["name"], "color": user["color"], "text": data.get("text", ""), "time": now}
    save_message(user["room"], msg["name"], msg["color"], msg["text"], msg["time"])
    emit("message", msg, to=user["room"])

@socketio.on("typing")
def on_typing(data):
    user = users.get(request.sid)
    if not user or not user.get("room"): return
    emit("typing", {"name": user["name"], "typing": data.get("typing", False)},
         to=user["room"], include_self=False)

@socketio.on("disconnect")
def on_disconnect():
    user = users.pop(request.sid, None)
    if user and user.get("room"):
        leave_room(user["room"])
        now = datetime.now().strftime("%H:%M")
        emit("message", {"type": "system", "text": f"{user['name']} left", "time": now}, to=user["room"])
        broadcast_users(user["room"])

def broadcast_users(room):
    room_users = [{"name": u["name"], "color": u["color"]}
                  for u in users.values() if u.get("room") == room]
    socketio.emit("users", room_users, to=room)

if __name__ == "__main__":
    socketio.run(app, debug=True, port=5000)
