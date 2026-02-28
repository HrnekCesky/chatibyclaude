from flask import Flask, render_template_string, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime
import sqlite3
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecret'
socketio = SocketIO(app, cors_allowed_origins="*")

# Track connected users: {sid: {name, room, color}}
users = {}

# ── Database setup ──
DB_PATH = "chat_history.db"

def init_db():
    """Create the messages table if it doesn't exist yet."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                room      TEXT    NOT NULL,
                name      TEXT    NOT NULL,
                color     TEXT    NOT NULL,
                text      TEXT    NOT NULL,
                time      TEXT    NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

def save_message(room, name, color, text, time):
    """Save one chat message to the database."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO messages (room, name, color, text, time) VALUES (?, ?, ?, ?, ?)",
            (room, name, color, text, time)
        )
        conn.commit()

def load_history(room, limit=100):
    """Load the last `limit` messages for a room."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, color, text, time FROM messages WHERE room=? ORDER BY id DESC LIMIT ?",
            (room, limit)
        ).fetchall()
    # Return oldest-first so they display in order
    return [dict(r) for r in reversed(rows)]

init_db()  # Run once on startup

COLORS = [
    "#e74c3c","#e67e22","#f1c40f","#2ecc71",
    "#1abc9c","#3498db","#9b59b6","#e91e63",
    "#00bcd4","#ff5722","#8bc34a","#ff9800"
]
color_index = 0

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
    --bg: #0d0d0d;
    --panel: #141414;
    --border: #252525;
    --accent: #c8f135;
    --accent2: #7effd4;
    --text: #f0f0f0;
    --muted: #555;
    --msg-bg: #1a1a1a;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; background: var(--bg); color: var(--text); font-family: 'DM Sans', sans-serif; overflow: hidden; }

  /* ── Join Screen ── */
  #join-screen {
    position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;
    background: var(--bg); z-index: 100;
  }
  .join-box {
    width: 420px; padding: 48px; border: 1px solid var(--border);
    background: var(--panel); position: relative; overflow: hidden;
  }
  .join-box::before {
    content: ''; position: absolute; top: -60px; right: -60px;
    width: 180px; height: 180px; border-radius: 50%;
    background: radial-gradient(circle, rgba(200,241,53,0.15) 0%, transparent 70%);
    pointer-events: none;
  }
  .join-box h1 {
    font-family: 'Space Mono', monospace; font-size: 1.8rem;
    color: var(--accent); letter-spacing: -1px; margin-bottom: 6px;
  }
  .join-box p { color: var(--muted); font-size: 0.9rem; margin-bottom: 32px; }
  .field { margin-bottom: 18px; }
  .field label { display: block; font-size: 0.75rem; letter-spacing: 1.5px; text-transform: uppercase; color: var(--muted); margin-bottom: 8px; }
  .field input {
    width: 100%; background: var(--bg); border: 1px solid var(--border);
    color: var(--text); padding: 12px 16px; font-size: 0.95rem;
    font-family: 'DM Sans', sans-serif; outline: none; transition: border-color 0.2s;
  }
  .field input:focus { border-color: var(--accent); }
  .join-btn {
    width: 100%; padding: 14px; background: var(--accent); color: #0d0d0d;
    font-family: 'Space Mono', monospace; font-size: 0.9rem; font-weight: 700;
    border: none; cursor: pointer; letter-spacing: 1px; text-transform: uppercase;
    transition: opacity 0.2s; margin-top: 8px;
  }
  .join-btn:hover { opacity: 0.85; }

  /* ── Main Layout ── */
  #app { display: none; height: 100vh; display: none; flex-direction: row; }
  #app.visible { display: flex; }

  /* Sidebar */
  .sidebar {
    width: 240px; min-width: 240px; background: var(--panel);
    border-right: 1px solid var(--border); display: flex; flex-direction: column;
  }
  .sidebar-header {
    padding: 20px 18px 16px; border-bottom: 1px solid var(--border);
  }
  .sidebar-header h2 {
    font-family: 'Space Mono', monospace; font-size: 1rem;
    color: var(--accent); letter-spacing: -0.5px;
  }
  .sidebar-header span { font-size: 0.78rem; color: var(--muted); }
  .sidebar-section { padding: 14px 18px 8px; }
  .sidebar-section h3 { font-size: 0.65rem; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); margin-bottom: 10px; }
  .user-list { flex: 1; overflow-y: auto; padding: 0 10px 10px; }
  .user-item {
    display: flex; align-items: center; gap: 9px;
    padding: 8px 8px; border-radius: 4px; font-size: 0.88rem;
    animation: fadeIn 0.3s ease;
  }
  .user-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
  .user-name { color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .user-name.me { font-weight: 600; }
  .room-tag {
    font-size: 0.7rem; color: var(--muted); padding: 2px 7px;
    border: 1px solid var(--border); border-radius: 2px; margin-left: auto; white-space: nowrap;
  }

  /* Chat area */
  .chat-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .chat-header {
    padding: 16px 24px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 14px; background: var(--panel);
  }
  .chat-header .room-name {
    font-family: 'Space Mono', monospace; font-size: 1rem; color: var(--text);
  }
  .chat-header .room-name span { color: var(--accent); }
  .online-badge {
    font-size: 0.72rem; background: rgba(200,241,53,0.12); color: var(--accent);
    padding: 3px 10px; border-radius: 20px; letter-spacing: 0.5px; margin-left: auto;
  }

  /* Messages */
  .messages { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 4px; }
  .messages::-webkit-scrollbar { width: 4px; }
  .messages::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  .msg { display: flex; flex-direction: column; max-width: 72%; animation: msgIn 0.25s ease; }
  .msg.own { align-self: flex-end; align-items: flex-end; }
  .msg.other { align-self: flex-start; align-items: flex-start; }
  .msg.system { align-self: center; align-items: center; max-width: 100%; }

  .msg-meta { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; padding: 0 4px; }
  .msg-author { font-size: 0.78rem; font-weight: 600; }
  .msg-time { font-size: 0.68rem; color: var(--muted); }

  .msg-bubble {
    padding: 10px 14px; font-size: 0.92rem; line-height: 1.55;
    border-radius: 2px; word-break: break-word; max-width: 100%;
  }
  .msg.other .msg-bubble { background: var(--msg-bg); border-left: 3px solid; }
  .msg.own .msg-bubble { background: rgba(200,241,53,0.12); border-right: 3px solid var(--accent); color: var(--text); }
  .msg.system .msg-bubble { background: transparent; color: var(--muted); font-size: 0.78rem; font-style: italic; padding: 4px 0; }

  /* consecutive messages */
  .msg + .msg.own, .msg + .msg.other { margin-top: 2px; }
  .msg.gap { margin-top: 14px; }

  @keyframes msgIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

  /* Input */
  .input-bar {
    padding: 16px 24px; border-top: 1px solid var(--border);
    display: flex; gap: 12px; align-items: flex-end; background: var(--panel);
  }
  .input-wrap { flex: 1; position: relative; }
  #msg-input {
    width: 100%; background: var(--bg); border: 1px solid var(--border);
    color: var(--text); padding: 12px 16px; font-size: 0.92rem;
    font-family: 'DM Sans', sans-serif; outline: none; resize: none;
    max-height: 120px; line-height: 1.5; transition: border-color 0.2s;
    border-radius: 2px;
  }
  #msg-input:focus { border-color: var(--accent); }
  .send-btn {
    padding: 12px 22px; background: var(--accent); color: #0d0d0d;
    font-family: 'Space Mono', monospace; font-size: 0.8rem; font-weight: 700;
    border: none; cursor: pointer; letter-spacing: 0.5px; text-transform: uppercase;
    transition: opacity 0.15s; border-radius: 2px; white-space: nowrap;
  }
  .send-btn:hover { opacity: 0.85; }
  .send-btn:active { transform: scale(0.97); }

  .typing-indicator { padding: 0 24px 8px; font-size: 0.75rem; color: var(--muted); min-height: 22px; font-style: italic; }
</style>
</head>
<body>

<!-- Join Screen -->
<div id="join-screen">
  <div class="join-box">
    <h1>LiveChat</h1>
    <p>Real-time multi-user chat. No account needed.</p>
    <div class="field">
      <label>Your name</label>
      <input type="text" id="username" placeholder="e.g. Alice" maxlength="24" />
    </div>
    <div class="field">
      <label>Room</label>
      <input type="text" id="room" placeholder="e.g. general" maxlength="32" value="general" />
    </div>
    <button class="join-btn" onclick="joinChat()">Join Room →</button>
  </div>
</div>

<!-- App -->
<div id="app">
  <div class="sidebar">
    <div class="sidebar-header">
      <h2>LiveChat</h2>
      <span id="my-room-label"></span>
    </div>
    <div class="sidebar-section">
      <h3>Online</h3>
    </div>
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
      <div class="input-wrap">
        <textarea id="msg-input" rows="1" placeholder="Message…"></textarea>
      </div>
      <button class="send-btn" onclick="sendMessage()">Send</button>
    </div>
  </div>
</div>

<script>
  const socket = io();
  let myName = '', myRoom = '', myColor = '#ffffff';
  let typingTimeout = null;
  let isTyping = false;
  let lastSender = null;

  // ── Join ──
  function joinChat() {
    const name = document.getElementById('username').value.trim();
    const room = document.getElementById('room').value.trim() || 'general';
    if (!name) { document.getElementById('username').focus(); return; }
    myName = name; myRoom = room;
    socket.emit('join', { name, room });
    document.getElementById('join-screen').style.display = 'none';
    document.getElementById('app').classList.add('visible');
    document.getElementById('room-label').textContent = room;
    document.getElementById('my-room-label').textContent = '#' + room;
    document.getElementById('msg-input').focus();
  }

  // ── Send ──
  function sendMessage() {
    const input = document.getElementById('msg-input');
    const text = input.value.trim();
    if (!text) return;
    socket.emit('message', { text });
    input.value = '';
    input.style.height = 'auto';
    clearTyping();
  }

  // ── Enter key ──
  document.getElementById('msg-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });

  // ── Typing detection ──
  document.getElementById('msg-input').addEventListener('input', () => {
    const ta = document.getElementById('msg-input');
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
    if (!isTyping) { isTyping = true; socket.emit('typing', { typing: true }); }
    clearTimeout(typingTimeout);
    typingTimeout = setTimeout(clearTyping, 1500);
  });

  function clearTyping() {
    if (isTyping) { isTyping = false; socket.emit('typing', { typing: false }); }
  }

  // ── Load history when joining ──
  socket.on('history', messages => {
    messages.forEach(data => {
      const msgs = document.getElementById('messages');
      const isOwn = data.name === myName;
      const isGap = lastSender !== data.name;
      lastSender = data.name;

      const div = document.createElement('div');
      div.className = 'msg ' + (isOwn ? 'own' : 'other') + (isGap ? ' gap' : '');
      div.style.animation = 'none'; // no animation for history
      div.innerHTML = `
        ${isGap ? `<div class="msg-meta">
          <span class="msg-author" style="color:${data.color}">${escHtml(data.name)}</span>
          <span class="msg-time">${data.time}</span>
        </div>` : ''}
        <div class="msg-bubble" ${!isOwn ? `style="border-color:${data.color}"` : ''}>${escHtml(data.text)}</div>
      `;
      msgs.appendChild(div);
    });
    // Add a divider after history
    if (messages.length > 0) {
      const divider = document.createElement('div');
      divider.className = 'msg system';
      divider.innerHTML = '<div class="msg-bubble">── you joined ──</div>';
      document.getElementById('messages').appendChild(divider);
    }
    document.getElementById('messages').scrollTop = 999999;
    lastSender = null; // reset so next live message shows its author
  });

  // ── Receive color on join ──
  socket.on('joined', data => {
    myColor = data.color;
  });

  // ── Render message ──
  socket.on('message', data => {
    const msgs = document.getElementById('messages');
    const isOwn = data.name === myName;
    const isSystem = data.type === 'system';
    const isGap = lastSender !== data.name;
    lastSender = isSystem ? null : data.name;

    const div = document.createElement('div');
    div.className = 'msg ' + (isSystem ? 'system' : (isOwn ? 'own' : 'other')) + (isGap && !isSystem ? ' gap' : '');

    if (isSystem) {
      div.innerHTML = `<div class="msg-bubble">${data.text}</div>`;
    } else {
      const showMeta = isGap;
      div.innerHTML = `
        ${showMeta ? `<div class="msg-meta">
          <span class="msg-author" style="color:${data.color}">${data.name}</span>
          <span class="msg-time">${data.time}</span>
        </div>` : ''}
        <div class="msg-bubble" ${!isOwn ? `style="border-color:${data.color}"` : ''}>${escHtml(data.text)}</div>
      `;
    }

    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
  });

  // ── User list ──
  socket.on('users', data => {
    const list = document.getElementById('user-list');
    list.innerHTML = '';
    data.forEach(u => {
      const el = document.createElement('div');
      el.className = 'user-item';
      const isMe = u.name === myName;
      el.innerHTML = `
        <div class="user-dot" style="background:${u.color}"></div>
        <div class="user-name ${isMe ? 'me' : ''}">${escHtml(u.name)}${isMe ? ' (you)' : ''}</div>
      `;
      list.appendChild(el);
    });
    const count = data.length;
    document.getElementById('online-count').textContent = count + ' online';
  });

  // ── Typing indicator ──
  let typingUsers = new Set();
  socket.on('typing', data => {
    if (data.typing) typingUsers.add(data.name);
    else typingUsers.delete(data.name);
    const others = [...typingUsers].filter(n => n !== myName);
    const el = document.getElementById('typing-indicator');
    if (others.length === 0) el.textContent = '';
    else if (others.length === 1) el.textContent = `${others[0]} is typing…`;
    else el.textContent = `${others.slice(0,-1).join(', ')} and ${others.at(-1)} are typing…`;
  });

  // ── Enter key on join screen ──
  document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && document.getElementById('join-screen').style.display !== 'none') {
      joinChat();
    }
  });

  function escHtml(t) {
    return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
  }
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)

@socketio.on("join")
def on_join(data):
    global color_index
    name = data.get("name", "Anonymous")[:24]
    room = data.get("room", "general")[:32]
    color = COLORS[color_index % len(COLORS)]
    color_index += 1

    users[request.sid] = {"name": name, "room": room, "color": color}
    join_room(room)

    emit("joined", {"color": color})

    # Send saved history only to the person who just joined
    history = load_history(room)
    if history:
        emit("history", history)

    now = datetime.now().strftime("%H:%M")
    emit("message", {"type": "system", "text": f"{name} joined the room", "time": now}, to=room)
    broadcast_users(room)

@socketio.on("message")
def on_message(data):
    user = users.get(request.sid)
    if not user: return
    now = datetime.now().strftime("%H:%M")
    msg = {
        "name": user["name"],
        "color": user["color"],
        "text": data.get("text", ""),
        "time": now
    }
    # Save to database before broadcasting
    save_message(user["room"], msg["name"], msg["color"], msg["text"], msg["time"])
    emit("message", msg, to=user["room"])

@socketio.on("typing")
def on_typing(data):
    user = users.get(request.sid)
    if not user: return
    emit("typing", {"name": user["name"], "typing": data.get("typing", False)},
         to=user["room"], include_self=False)

@socketio.on("disconnect")
def on_disconnect():
    user = users.pop(request.sid, None)
    if user:
        leave_room(user["room"])
        now = datetime.now().strftime("%H:%M")
        emit("message", {"type": "system", "text": f"{user['name']} left the room", "time": now}, to=user["room"])
        broadcast_users(user["room"])

def broadcast_users(room):
    room_users = [{"name": u["name"], "color": u["color"]} for u in users.values() if u["room"] == room]
    socketio.emit("users", room_users, to=room)

if __name__ == "__main__":
    socketio.run(app, debug=True, port=5000)