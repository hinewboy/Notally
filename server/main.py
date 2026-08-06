"""
樱花便签云同步后端
- 用户注册/登录（JWT token）
- 笔记上传/下载（按用户隔离）
- 网页端浏览与导出
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

BASE_DIR = Path("/opt/notebook-sync")
DB_PATH = BASE_DIR / "data" / "sync.db"
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = Path(__file__).resolve().parent / "static"
SECRET_KEY = os.environ.get("SECRET_KEY", "sakura-notebook-secret-change-me")
TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days

app = FastAPI(title="樱花便签云同步", docs_url=None, redoc_url=None)


def get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            note_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    # 迁移：旧库无 is_admin 列时补充
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "is_admin" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE users SET is_admin = 1 WHERE username = 'admin'")
        conn.commit()
    conn.close()


def ensure_admin():
    """确保存在 admin 管理员账号（从环境变量读取密码，默认 admin123456）"""
    admin_pass = os.environ.get("ADMIN_PASSWORD", "admin123456")
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, 1, ?)",
                ("admin", hash_password(admin_pass), int(time.time() * 1000)),
            )
            conn.commit()
    finally:
        conn.close()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
        return hmac.compare_digest(calc, digest)
    except Exception:
        return False


def make_token(user_id: int, username: str) -> str:
    payload = f"{user_id}.{username}.{int(time.time())}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{sig}"


def verify_token(token: str) -> Optional[dict]:
    try:
        parts = token.split(".")
        if len(parts) != 4:
            return None
        user_id, username, ts, sig = parts
        payload = f"{user_id}.{username}.{ts}"
        calc = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(calc, sig):
            return None
        if int(time.time()) - int(ts) > TOKEN_TTL:
            return None
        return {"id": int(user_id), "username": username}
    except Exception:
        return None


class AuthRequest(BaseModel):
    username: str
    password: str


class NotesRequest(BaseModel):
    notes: List[dict]


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    user = verify_token(authorization[7:])
    if not user:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return user


def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    """要求当前用户为管理员（从数据库实时校验）"""
    conn = get_db()
    try:
        row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not row or not row["is_admin"]:
            raise HTTPException(status_code=403, detail="需要管理员权限")
        return user
    finally:
        conn.close()


@app.get("/api/admin/users")
def admin_list_users(admin: dict = Depends(get_admin_user)):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT u.id, u.username, u.is_admin, u.created_at, COUNT(n.id) AS note_count "
            "FROM users u LEFT JOIN notes n ON n.user_id = u.id GROUP BY u.id ORDER BY u.id"
        ).fetchall()
        return {"users": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/api/admin/users/{user_id}/notes")
def admin_user_notes(user_id: int, admin: dict = Depends(get_admin_user)):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT note_json FROM notes WHERE user_id = ? ORDER BY rowid", (user_id,)
        ).fetchall()
        return {"notes": [json.loads(r["note_json"]) for r in rows], "count": len(rows)}
    finally:
        conn.close()


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, admin: dict = Depends(get_admin_user)):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="不能删除自己的管理员账号")
    conn = get_db()
    try:
        conn.execute("DELETE FROM notes WHERE user_id = ?", (user_id,))
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="用户不存在")
        return {"deleted": True}
    finally:
        conn.close()


@app.post("/api/login")
def login(body: AuthRequest):
    username = body.username.strip()
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        return {
            "token": make_token(row["id"], username),
            "username": username,
            "is_admin": bool(row["is_admin"]),
        }
    finally:
        conn.close()


@app.post("/api/register")
def register(body: AuthRequest):
    username = body.username.strip()
    password = body.password
    if not username or len(username) < 2 or len(username) > 32:
        raise HTTPException(status_code=400, detail="用户名需为 2-32 个字符")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if username.lower() == "admin":
        raise HTTPException(status_code=400, detail="该用户名不可用")

    conn = get_db()
    try:
        cur = conn.execute("SELECT id FROM users WHERE username = ?", (username,))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="用户名已被注册")
        created = int(time.time() * 1000)
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, hash_password(password), created),
        )
        user_id = cur.lastrowid
        conn.commit()
        return {"token": make_token(user_id, username), "username": username, "is_admin": False}
    finally:
        conn.close()


@app.put("/api/notes")
def upload_notes(body: NotesRequest, user: dict = Depends(get_current_user)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM notes WHERE user_id = ?", (user["id"],))
        now = int(time.time() * 1000)
        for note in body.notes:
            conn.execute(
                "INSERT INTO notes (user_id, note_json, updated_at) VALUES (?, ?, ?)",
                (user["id"], json.dumps(note, ensure_ascii=False), now),
            )
        conn.commit()
        return {"count": len(body.notes)}
    finally:
        conn.close()


@app.get("/api/notes")
def download_notes(user: dict = Depends(get_current_user)):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT note_json FROM notes WHERE user_id = ? ORDER BY rowid", (user["id"],)
        ).fetchall()
        notes = [json.loads(r["note_json"]) for r in rows]
        return {"notes": notes, "count": len(notes)}
    finally:
        conn.close()


@app.get("/api/export")
def export_notes(user: dict = Depends(get_current_user)):
    """导出全部笔记为纯文本（网页下载用）"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT note_json FROM notes WHERE user_id = ? ORDER BY rowid", (user["id"],)
        ).fetchall()
        lines = []
        for r in rows:
            note = json.loads(r["note_json"])
            title = note.get("title") or "（无标题）"
            lines.append(f"===== {title} =====")
            if note.get("body"):
                lines.append(note["body"])
            for item in note.get("items", []):
                check = "✓" if item.get("checked") else "☐"
                lines.append(f"{check} {item.get('body', '')}")
            ts = note.get("timestamp", 0)
            if ts:
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone()
                lines.append(f"[{dt.strftime('%Y-%m-%d %H:%M')}]")
            lines.append("")
        return PlainTextResponse("\n".join(lines), media_type="text/plain; charset=utf-8")
    finally:
        conn.close()


WEB_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>樱花便签 · 云同步</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<meta name="theme-color" content="#FF8FAB">
<style>
  :root { --pink: #FF8FAB; --pink-dark: #E56E8F; --bg: #FFF5F7; --card: #fff; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: #333; min-height: 100vh; }
  .header { background: linear-gradient(135deg, var(--pink), #FFB7C5); color: #fff; padding: 32px 20px; text-align: center; }
  .header h1 { font-size: 28px; margin-bottom: 6px; letter-spacing: 2px; }
  .header p { opacity: 0.92; font-size: 14px; }
  .container { max-width: 720px; margin: 0 auto; padding: 20px 16px 60px; }
  .card { background: var(--card); border-radius: 14px; padding: 20px; box-shadow: 0 2px 12px rgba(255,143,171,.18); margin-bottom: 16px; }
  .card h2 { font-size: 18px; color: var(--pink-dark); margin-bottom: 14px; }
  input { width: 100%; padding: 12px 14px; border: 1.5px solid #f0c4cf; border-radius: 10px; font-size: 15px; margin-bottom: 10px; outline: none; }
  input:focus { border-color: var(--pink); }
  .btn { display: inline-block; background: var(--pink); color: #fff; border: none; padding: 12px 22px; border-radius: 10px; font-size: 15px; cursor: pointer; transition: background .2s; }
  .btn:hover { background: var(--pink-dark); }
  .btn.secondary { background: #fff; color: var(--pink-dark); border: 1.5px solid var(--pink); }
  .row { display: flex; gap: 10px; flex-wrap: wrap; }
  .note { border-left: 4px solid var(--pink); padding: 12px 14px; margin-bottom: 10px; background: #fff; border-radius: 0 10px 10px 0; box-shadow: 0 1px 6px rgba(0,0,0,.05); }
  .note h3 { font-size: 16px; margin-bottom: 4px; }
  .note .meta { font-size: 12px; color: #999; margin-bottom: 6px; }
  .note .body { font-size: 14px; white-space: pre-wrap; color: #555; }
  .note .item { font-size: 14px; color: #555; }
  .count { font-size: 13px; color: #999; margin-bottom: 10px; }
  .hidden { display: none; }
  .error { color: #d33; font-size: 13px; margin-top: 6px; }
  .ok { color: #2a8; font-size: 13px; margin-top: 6px; }
  .empty { text-align: center; color: #bbb; padding: 30px 0; }
  a { color: var(--pink-dark); }
</style>
</head>
<body>
<div class="header">
  <h1>🌸 樱花便签</h1>
  <p>云同步 · 网页浏览 · 笔记导出</p>
</div>
<div class="container">
  <div class="card" id="authCard">
    <h2 id="authTitle">登录</h2>
    <input id="username" placeholder="用户名" autocomplete="username">
    <input id="password" type="password" placeholder="密码" autocomplete="current-password">
    <div class="row">
      <button class="btn" onclick="doAuth(false)">登录</button>
      <button class="btn secondary" onclick="doAuth(true)">注册</button>
    </div>
    <div id="authMsg" class="error"></div>
  </div>

  <div class="card hidden" id="notesCard">
    <h2>我的笔记</h2>
    <div class="row">
      <button class="btn" onclick="logout()">退出登录</button>
      <button class="btn secondary" onclick="exportTxt()">导出 TXT</button>
      <button class="btn secondary" onclick="exportJson()">导出 JSON</button>
    </div>
    <div id="count" class="count"></div>
    <div id="notes"></div>
  </div>

  <div class="card hidden" id="adminCard">
    <h2>👑 用户管理</h2>
    <div id="adminMsg" class="ok"></div>
    <div id="adminUsers"></div>
  </div>
</div>

<script>
let token = localStorage.getItem('token') || '';
let username = localStorage.getItem('username') || '';
let isAdmin = localStorage.getItem('is_admin') === '1';

function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (token) headers['Authorization'] = 'Bearer ' + token;
  if (opts.body) headers['Content-Type'] = 'application/json';
  return fetch(path, { ...opts, headers });
}

async function doAuth(register) {
  const msg = document.getElementById('authMsg');
  msg.className = 'error';
  msg.textContent = '';
  const u = document.getElementById('username').value.trim();
  const p = document.getElementById('password').value;
  if (!u || p.length < 6) { msg.textContent = '用户名不能为空，密码至少 6 位'; return; }
  try {
    const res = await api('/api/' + (register ? 'register' : 'login'), {
      method: 'POST',
      body: JSON.stringify({ username: u, password: p })
    });
    const data = await res.json();
    if (!res.ok) { msg.textContent = data.detail || '请求失败'; return; }
    token = data.token; username = data.username;
    isAdmin = !!data.is_admin;
    localStorage.setItem('token', token);
    localStorage.setItem('username', username);
    localStorage.setItem('is_admin', isAdmin ? '1' : '0');
    loadNotes();
  } catch (e) { msg.textContent = '网络错误'; }
}

function logout() {
  token = ''; username = ''; isAdmin = false;
  localStorage.removeItem('token');
  localStorage.removeItem('username');
  localStorage.removeItem('is_admin');
  location.reload();
}

async function loadNotes() {
  document.getElementById('authCard').classList.add('hidden');
  document.getElementById('notesCard').classList.remove('hidden');
  if (isAdmin) {
    document.getElementById('adminCard').classList.remove('hidden');
    loadAdminUsers();
  }
  const res = await api('/api/notes');
  const data = await res.json();
  const box = document.getElementById('notes');
  document.getElementById('count').textContent = '共 ' + (data.count || 0) + ' 条笔记';
  if (!data.notes || data.notes.length === 0) {
    box.innerHTML = '<div class="empty">还没有笔记，去 App 里同步一下吧 🌸</div>';
    return;
  }
  box.innerHTML = data.notes.map(note => {
    let html = '<div class="note"><h3>' + esc(note.title || '无标题') + '</h3>';
    const dt = new Date(note.timestamp || Date.now());
    html += '<div class="meta">' + dt.toLocaleString('zh-CN') + '</div>';
    if (note.body) html += '<div class="body">' + esc(note.body) + '</div>';
    (note.items || []).forEach(it => {
      html += '<div class="item">' + (it.checked ? '✅' : '⬜') + ' ' + esc(it.body || '') + '</div>';
    });
    html += '</div>';
    return html;
  }).join('');
}

function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function download(filename, content) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ===== 管理员面板 ===== */
async function loadAdminUsers() {
  const box = document.getElementById('adminUsers');
  const msg = document.getElementById('adminMsg');
  msg.textContent = '';
  try {
    const res = await api('/api/admin/users');
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      msg.className = 'error';
      msg.textContent = d.detail || '加载失败';
      return;
    }
    const data = await res.json();
    if (!data.users || data.users.length === 0) {
      box.innerHTML = '<div class="empty">暂无用户</div>';
      return;
    }
    box.innerHTML = data.users.map(u => {
      const created = new Date(u.created_at).toLocaleString('zh-CN');
      const badge = u.is_admin ? ' <span style="color:#E56E8F">👑 管理员</span>' : '';
      return '<div class="note"><h3>' + esc(u.username) + badge +
        ' <span class="meta">#' + u.id + ' · ' + (u.note_count || 0) + ' 条笔记 · ' + created + '</span></h3>' +
        '<div class="row"><button class="btn secondary" onclick="adminViewNotes(' + u.id + ')">查看笔记</button>' +
        (u.is_admin ? '' : '<button class="btn" onclick="adminDeleteUser(' + u.id + ')">删除用户</button>') +
        '</div></div>';
    }).join('');
  } catch (e) {
    msg.className = 'error';
    msg.textContent = '网络错误';
  }
}

async function adminViewNotes(userId) {
  const msg = document.getElementById('adminMsg');
  try {
    const res = await api('/api/admin/users/' + userId + '/notes');
    const data = await res.json();
    if (!res.ok) {
      msg.className = 'error';
      msg.textContent = data.detail || '加载失败';
      return;
    }
    if (!data.notes || data.notes.length === 0) {
      alert('该用户暂无笔记');
      return;
    }
    const txt = data.notes.map(n => {
      let s = '===== ' + (n.title || '无标题') + ' =====\\n';
      if (n.body) s += n.body + '\\n';
      (n.items || []).forEach(it => { s += (it.checked ? '[x] ' : '[ ] ') + (it.body || '') + '\\n'; });
      return s;
    }).join('\\n');
    if (confirm('查看用户 ' + userId + ' 的 ' + data.count + ' 条笔记，是否下载为 TXT？')) {
      download('user-' + userId + '-notes.txt', txt);
    }
  } catch (e) {
    msg.className = 'error';
    msg.textContent = '网络错误';
  }
}

async function adminDeleteUser(userId) {
  if (!confirm('确定删除用户 #' + userId + '？其所有笔记将一并删除！')) return;
  const msg = document.getElementById('adminMsg');
  try {
    const res = await api('/api/admin/users/' + userId, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      msg.className = 'error';
      msg.textContent = data.detail || '删除失败';
      return;
    }
    msg.className = 'ok';
    msg.textContent = '用户已删除';
    loadAdminUsers();
  } catch (e) {
    msg.className = 'error';
    msg.textContent = '网络错误';
  }
}

async function exportTxt() {
  const res = await api('/api/export');
  const text = await res.text();
  download('樱花便签-' + username + '-' + new Date().toISOString().slice(0,10) + '.txt', text);
}

async function exportJson() {
  const res = await api('/api/notes');
  const data = await res.json();
  download('樱花便签-' + username + '-' + new Date().toISOString().slice(0,10) + '.json',
    JSON.stringify(data.notes, null, 2));
}

if (token) { loadNotes(); }
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def home():
    return WEB_PAGE


@app.get("/favicon.svg")
def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
def health():
    return {"status": "ok"}


init_db()
ensure_admin()