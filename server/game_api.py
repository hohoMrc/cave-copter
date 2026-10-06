"""穿岩直升機的排行榜 API。

只用 Python 內建模組（http.server + sqlite3），不需要安裝套件，記憶體用量很小。
放在 Caddy 後面，對外路徑是 https://hoho-stock.duckdns.org/game-api/

  POST /runs     開始一局：遊玩次數 +1，回傳這局的 run_id
  GET  /stats    遊玩次數與前 10 名
  POST /scores   上傳成績 {run_id, name, distance}
"""

import json
import os
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8002"))
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db"))
ALLOWED_ORIGINS = {
    "https://hohomrc.github.io",
    "http://localhost:8080",  # 本機測試用
}
TOP_N = 10
NAME_MAX = 12
# 遊戲最高速度約 430 單位/秒 ÷ 12 單位/公尺 ≈ 36 m/s，給一點寬容
MAX_METERS_PER_SEC = 40
RUNS_PER_MINUTE = 20  # 每個 IP 每分鐘最多開幾局

db_lock = threading.Lock()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.executescript("""
    PRAGMA journal_mode = WAL;
    CREATE TABLE IF NOT EXISTS counter (id INTEGER PRIMARY KEY CHECK (id = 1), plays INTEGER NOT NULL);
    INSERT OR IGNORE INTO counter (id, plays) VALUES (1, 0);
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY, started REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS scores (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, distance INTEGER NOT NULL, created REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS scores_distance ON scores (distance DESC);
""")

# 簡單的記憶體內限流：{ip: [時間戳記...]}
recent_runs: dict[str, list[float]] = {}


def rate_limited(ip: str) -> bool:
    now = time.time()
    stamps = [t for t in recent_runs.get(ip, []) if now - t < 60]
    limited = len(stamps) >= RUNS_PER_MINUTE
    if not limited:
        stamps.append(now)
    recent_runs[ip] = stamps
    return limited


def top_scores():
    rows = db.execute(
        "SELECT name, distance FROM scores ORDER BY distance DESC, created ASC LIMIT ?", (TOP_N,)
    ).fetchall()
    return [{"name": n, "distance": d} for n, d in rows]


def stats():
    plays = db.execute("SELECT plays FROM counter WHERE id = 1").fetchone()[0]
    return {"plays": plays, "top": top_scores()}


def clean_name(raw) -> str:
    if not isinstance(raw, str):
        return ""
    # 拿掉控制字元與前後空白，限制長度
    name = "".join(ch for ch in raw if ch.isprintable()).strip()
    return name[:NAME_MAX]


class Handler(BaseHTTPRequestHandler):
    server_version = "game-api"

    def client_ip(self) -> str:
        # Caddy 會把真實 IP 放在 X-Forwarded-For
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def send_json(self, status: int, body: dict):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1024:
            return {}
        try:
            body = json.loads(self.rfile.read(length))
            return body if isinstance(body, dict) else {}
        except ValueError:
            return {}

    def do_OPTIONS(self):
        # CORS 預檢請求
        self.send_response(204)
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")
            self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/stats":
            with db_lock:
                return self.send_json(200, stats())
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/runs":
            return self.start_run()
        if path == "/scores":
            return self.submit_score()
        self.send_json(404, {"error": "not found"})

    def start_run(self):
        if rate_limited(self.client_ip()):
            return self.send_json(429, {"error": "開局太頻繁，請稍後再試"})
        run_id = secrets.token_urlsafe(16)
        now = time.time()
        with db_lock, db:
            db.execute("UPDATE counter SET plays = plays + 1 WHERE id = 1")
            db.execute("INSERT INTO runs (id, started) VALUES (?, ?)", (run_id, now))
            # 順便清掉一天前的舊紀錄，資料表不會一直長大
            db.execute("DELETE FROM runs WHERE started < ?", (now - 86400,))
            plays = db.execute("SELECT plays FROM counter WHERE id = 1").fetchone()[0]
        self.send_json(200, {"run_id": run_id, "plays": plays})

    def submit_score(self):
        body = self.read_json()
        run_id, name, distance = body.get("run_id"), clean_name(body.get("name")), body.get("distance")
        if not name:
            return self.send_json(400, {"error": "請輸入名字"})
        if not isinstance(distance, int) or distance <= 0:
            return self.send_json(400, {"error": "距離格式不對"})
        now = time.time()
        with db_lock, db:
            row = db.execute("SELECT started, used FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not row or row[1]:
                return self.send_json(400, {"error": "這局已經上傳過，或已經過期"})
            # 防作弊：這段時間內不可能飛得比最高速度還遠
            if distance > (now - row[0]) * MAX_METERS_PER_SEC + 20:
                return self.send_json(400, {"error": "距離不合理"})
            db.execute("UPDATE runs SET used = 1 WHERE id = ?", (run_id,))
            db.execute("INSERT INTO scores (name, distance, created) VALUES (?, ?, ?)", (name, distance, now))
            # 只保留前 100 名
            db.execute("DELETE FROM scores WHERE id NOT IN (SELECT id FROM scores ORDER BY distance DESC, created ASC LIMIT 100)")
            result = stats()
        self.send_json(200, result)

    def log_message(self, fmt, *args):
        # 交給 systemd journal，前面加上真實 IP
        print(f"{self.client_ip()} {fmt % args}", flush=True)


if __name__ == "__main__":
    print(f"game-api listening on 127.0.0.1:{PORT}, db={DB_PATH}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
