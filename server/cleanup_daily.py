#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""樱花便签每日维护：孤儿附件清理 + 空间统计 + 磁盘告警
部署: /opt/notebook-sync/cleanup_daily.py
crontab: 30 3 * * * /usr/bin/python3 /opt/notebook-sync/cleanup_daily.py >> /opt/notebook-sync/data/cleanup.log 2>&1
"""
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

BASE = Path("/opt/notebook-sync")
DATA = BASE / "data"
DB = DATA / "sync.db"
FILES_ROOT = DATA / "files"
PROJECT_QUOTA = 1024 * 1024 * 1024  # 1GB，与 main.py 保持一致
ALERT_LOG = DATA / "disk_alert.log"

# 1) 孤儿附件清理：删除未被任何笔记引用的文件
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
refs = {}
for r in conn.execute("SELECT user_id, note_json FROM notes"):
    uid = r["user_id"]
    refs.setdefault(uid, set())
    note = json.loads(r["note_json"])
    for key in ("images", "audios"):
        v = note.get(key, [])
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                v = []
        for item in v:
            if isinstance(item, dict) and item.get("name"):
                refs[uid].add(Path(item["name"]).name)
conn.close()

removed = 0
if FILES_ROOT.exists():
    for d in FILES_ROOT.iterdir():
        if not d.is_dir():
            continue
        try:
            uid = int(d.name)
        except ValueError:
            continue
        ref = refs.get(uid, set())
        for f in d.iterdir():
            if f.is_file() and f.name not in ref:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass

# 2) 附件总占用
total = (
    sum(f.stat().st_size for f in FILES_ROOT.rglob("*") if f.is_file())
    if FILES_ROOT.exists()
    else 0
)

# 3) 磁盘使用率
use_pct = 0
try:
    df = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=10).stdout
    use_pct = int(df.splitlines()[1].split()[4].rstrip("%"))
except Exception:
    pass

ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
line = f"[{ts}] 孤儿清理: {removed} 个; 附件占用: {total / 1024 / 1024:.1f}MB / {PROJECT_QUOTA / 1024 / 1024 / 1024:.1f}GB; 磁盘: {use_pct}%"
print(line)

alerts = []
if total > PROJECT_QUOTA * 0.9:
    alerts.append(f"[{ts}] !! 附件占用超过全局配额 90%（{total / 1024 / 1024 / 1024:.2f}GB / 1GB），请检查")
if use_pct >= 85:
    alerts.append(f"[{ts}] !! 磁盘使用率 {use_pct}% >= 85%，请清理服务器")
if alerts:
    with open(ALERT_LOG, "a", encoding="utf-8") as fh:
        fh.write("\n".join(alerts) + "\n")
    print("\n".join(alerts))
