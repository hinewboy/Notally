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
    attachments: List[dict] = []


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

        # 保存附件文件: data/files/<user_id>/<name>
        files_dir = DATA_DIR / "files" / str(user["id"])
        files_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for att in body.attachments:
            name = att.get("name", "")
            data = att.get("data", "")
            if not name or not data:
                continue
            # 防路径穿越
            safe = Path(name).name
            try:
                raw = __import__("base64").b64decode(data)
                (files_dir / safe).write_bytes(raw)
                saved += 1
            except Exception:
                continue
        return {"count": len(body.notes), "attachments_saved": saved}
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
        # 为每条笔记附加文件 URL 映射（供网页端渲染）
        base = f"/api/file/{user['id']}"
        for note in notes:
            images = note.get("images", [])
            if isinstance(images, str):
                try:
                    images = json.loads(images)
                except Exception:
                    images = []
            for img in images:
                name = img.get("name") if isinstance(img, dict) else None
                if name:
                    img["url"] = f"{base}/{Path(name).name}"
            audios = note.get("audios", [])
            if isinstance(audios, str):
                try:
                    audios = json.loads(audios)
                except Exception:
                    audios = []
            for au in audios:
                name = au.get("name") if isinstance(au, dict) else None
                if name:
                    au["url"] = f"{base}/{Path(name).name}"
        return {"notes": notes, "count": len(notes)}
    finally:
        conn.close()


@app.get("/api/file/{user_id}/{filename}")
def get_file(user_id: int, filename: str):
    """附件文件访问（URL 含用户ID，未公开索引，简单防护）"""
    safe = Path(filename).name
    path = DATA_DIR / "files" / str(user_id) / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    # 按扩展名推断 MIME
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg", ".mp4": "video/mp4",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=mime)


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





@app.get("/", response_class=HTMLResponse)
def home():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse("<h1>樱花便签</h1><p>页面文件缺失</p>")


@app.get("/favicon.svg")
def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
def health():
    return {"status": "ok"}


init_db()
ensure_admin()