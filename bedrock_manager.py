# -*- coding: utf-8 -*-
"""
LatinBat Bedrock Manager (V2) - single-file, production-oriented.
Features:
- Start/Stop/Restart server (soft /stop and hard terminate)
- Live console output viewer + log parser
- Discord webhook (secure tokenization), embeds configurable
- Message editor with emoji + placeholders + preview
- MySQL stats (players/sessions/daily), auto-create tables, reconnect
- Daily stats scheduler (configurable time)
- Automatic backups (zip), rotation, manual backup, exclusions
- EXE-friendly paths (PyInstaller)
"""

import os
import sys
import re
import time
import json
import base64
import zipfile
import shutil
import threading
import subprocess
import configparser
from dataclasses import dataclass
from datetime import datetime, date, timedelta

import requests
import mysql.connector

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ---------------------- EXE PATHS ----------------------
def app_dir() -> str:
    # PyInstaller support
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = app_dir()
CONFIG_FILE = os.path.join(BASE_DIR, "config.ini")

# ---------------------- SECURITY (Webhook tokenization) ----------------------
# Goal: avoid plain webhook in config.ini. This is obfuscation (not cryptographic security),
# but good enough to prevent casual copying. For stronger: Windows DPAPI / keyring (would add deps).
def machine_salt() -> str:
    # stable-ish salt
    return f"{os.getenv('COMPUTERNAME','pc')}|{os.getenv('USERNAME','user')}|LatinBat"

def xor_bytes(data: bytes, key: bytes) -> bytes:
    out = bytearray(len(data))
    for i, b in enumerate(data):
        out[i] = b ^ key[i % len(key)]
    return bytes(out)

def protect(text: str, key_hint: str) -> str:
    if not text:
        return ""
    key = (key_hint + "|" + machine_salt()).encode("utf-8")
    raw = text.encode("utf-8")
    enc = xor_bytes(raw, key)
    return base64.urlsafe_b64encode(enc).decode("ascii")

def unprotect(token: str, key_hint: str) -> str:
    if not token:
        return ""
    try:
        key = (key_hint + "|" + machine_salt()).encode("utf-8")
        enc = base64.urlsafe_b64decode(token.encode("ascii"))
        raw = xor_bytes(enc, key)
        return raw.decode("utf-8")
    except Exception:
        return ""  # fail closed

# ---------------------- CONFIG DEFAULTS ----------------------
DEFAULTS = {
    "SERVER": {
        "exe_path": "bedrock_server.exe",
        "workdir": "",  # empty means BASE_DIR
        "hard_kill_tree_windows": "false",
        "encoding": "utf-8",
    },
    "DISCORD": {
        "webhook_token": "",      # protected()
        "webhook_key_hint": "LatinBatKey",
        "enabled": "true",
        "mode": "embed",          # embed|text
        "username": "LatinBat Bot",
        "avatar_url": "",
        "rate_limit_seconds": "2"
    },
    "MESSAGES": {
        "join": "🟢 {player} entró al servidor",
        "leave": "🔴 {player} salió del servidor",
        "start": "🚀 Servidor iniciado",
        "stop_hard": "⛔ Servidor detenido bruscamente",
        "stop_soft": "🛑 Servidor detenido con /stop",
        "backup_done": "📦 Backup creado: {file}",
        "error": "🚨 Error detectado: {line}",
        "daily_title": "📊 Estadísticas diarias ({date})"
    },
    "EMBED": {
        "enabled": "true",
        "color": "3447003",
        "footer": "LatinBat Bedrock Server",
    },
    "MYSQL": {
        "enabled": "true",
        "host": "localhost",
        "user": "bedrock_srv",
        "password": "",
        "database": "latinbat_bedrock",
        "port": "3306",
    },
    "PARSER": {
        "anti_spam_seconds": "5",
        "detect_errors": "true",
    },
    "DAILY": {
        "enabled": "true",
        "hour": "0",
        "minute": "5",
        "top_n": "5"
    },
    "BACKUP": {
        "enabled": "true",
        "interval_minutes": "60",
        "path": "backups",
        "keep_last": "20",
        "include_worlds": "true",
        "include_config": "true",
        "exclude_patterns": ".tmp;.lock",
    }
}

def ensure_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not os.path.exists(CONFIG_FILE):
        for sec, kv in DEFAULTS.items():
            cfg[sec] = dict(kv)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            cfg.write(f)
    cfg.read(CONFIG_FILE, encoding="utf-8")
    # ensure missing keys get filled
    changed = False
    for sec, kv in DEFAULTS.items():
        if sec not in cfg:
            cfg[sec] = {}
            changed = True
        for k, v in kv.items():
            if k not in cfg[sec]:
                cfg[sec][k] = v
                changed = True
    if changed:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            cfg.write(f)
    return cfg

config = ensure_config()

def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        config.write(f)

# ---------------------- DISCORD CLIENT ----------------------
class DiscordClient:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg
        self._last_sent = 0.0

    def webhook_url(self) -> str:
        token = self.cfg["DISCORD"].get("webhook_token", "")
        hint = self.cfg["DISCORD"].get("webhook_key_hint", "LatinBatKey")
        return unprotect(token, hint)

    def set_webhook_url(self, url: str):
        hint = self.cfg["DISCORD"].get("webhook_key_hint", "LatinBatKey")
        self.cfg["DISCORD"]["webhook_token"] = protect(url.strip(), hint)

    def enabled(self) -> bool:
        return self.cfg["DISCORD"].getboolean("enabled", fallback=True)

    def _rate_limit_ok(self) -> bool:
        try:
            gap = int(self.cfg["DISCORD"].get("rate_limit_seconds", "2"))
        except ValueError:
            gap = 2
        now = time.time()
        if now - self._last_sent >= gap:
            self._last_sent = now
            return True
        return False

    def send(self, payload: dict):
        if not self.enabled():
            return
        if not self._rate_limit_ok():
            return
        url = self.webhook_url()
        if not url:
            return
        try:
            username = self.cfg["DISCORD"].get("username", "").strip()
            avatar_url = self.cfg["DISCORD"].get("avatar_url", "").strip()
            if username:
                payload["username"] = username
            if avatar_url:
                payload["avatar_url"] = avatar_url
            requests.post(url, json=payload, timeout=6)
        except Exception:
            pass

    def send_text(self, text: str):
        self.send({"content": text})

    def send_embed(self, title: str, fields: list):
        if not self.cfg["EMBED"].getboolean("enabled", fallback=True) or self.cfg["DISCORD"].get("mode","embed") != "embed":
            self.send_text(title)
            return
        try:
            color = int(self.cfg["EMBED"].get("color", "3447003"))
        except ValueError:
            color = 3447003
        footer = self.cfg["EMBED"].get("footer", "LatinBat Bedrock Server")
        self.send({
            "embeds": [{
                "title": title,
                "color": color,
                "fields": fields,
                "footer": {"text": footer}
            }]
        })

discord = DiscordClient(config)

# ---------------------- MYSQL MANAGER ----------------------
class MySQLManager:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg
        self.conn = None
        self.lock = threading.Lock()

    def enabled(self) -> bool:
        return self.cfg["MYSQL"].getboolean("enabled", fallback=True)

    def connect(self):
        if not self.enabled():
            return
        with self.lock:
            if self.conn and self.conn.is_connected():
                return
            self.conn = mysql.connector.connect(
                host=self.cfg["MYSQL"]["host"],
                user=self.cfg["MYSQL"]["user"],
                password=self.cfg["MYSQL"]["password"],
                database=self.cfg["MYSQL"]["database"],
                port=int(self.cfg["MYSQL"].get("port", "3306")),
                autocommit=True
            )

    def cursor(self):
        self.connect()
        if not self.conn:
            return None
        return self.conn.cursor()

    def ensure_tables(self):
        if not self.enabled():
            return
        cur = self.cursor()
        if not cur:
            return
        cur.execute("""
        CREATE TABLE IF NOT EXISTS players (
          xuid VARCHAR(32) PRIMARY KEY,
          name VARCHAR(32) NOT NULL,
          first_seen DATETIME NOT NULL,
          last_seen DATETIME NOT NULL,
          total_seconds BIGINT NOT NULL DEFAULT 0
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          xuid VARCHAR(32) NOT NULL,
          join_time DATETIME NOT NULL,
          leave_time DATETIME NULL,
          session_seconds INT NOT NULL DEFAULT 0,
          INDEX (xuid),
          INDEX (join_time),
          FOREIGN KEY (xuid) REFERENCES players(xuid) ON DELETE CASCADE
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
          stat_date DATE PRIMARY KEY,
          unique_players INT NOT NULL DEFAULT 0,
          total_seconds BIGINT NOT NULL DEFAULT 0,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)

    # ---- player/session operations ----
    def on_join(self, name: str, xuid: str, ts: datetime):
        if not self.enabled():
            return
        cur = self.cursor()
        if not cur:
            return
        cur.execute("""
            INSERT INTO players (xuid,name,first_seen,last_seen,total_seconds)
            VALUES (%s,%s,%s,%s,0)
            ON DUPLICATE KEY UPDATE name=%s, last_seen=%s
        """, (xuid, name, ts, ts, name, ts))
        cur.execute("""
            INSERT INTO sessions (xuid, join_time)
            VALUES (%s, %s)
        """, (xuid, ts))

    def on_leave(self, xuid: str, ts: datetime, session_seconds: int):
        if not self.enabled():
            return
        cur = self.cursor()
        if not cur:
            return
        cur.execute("""
            UPDATE sessions
            SET leave_time=%s, session_seconds=%s
            WHERE xuid=%s AND leave_time IS NULL
            ORDER BY id DESC
            LIMIT 1
        """, (ts, session_seconds, xuid))
        cur.execute("""
            UPDATE players
            SET total_seconds = total_seconds + %s,
                last_seen=%s
            WHERE xuid=%s
        """, (session_seconds, ts, xuid))

    def fetch_players(self):
        if not self.enabled():
            return []
        cur = self.cursor()
        if not cur:
            return []
        cur.execute("SELECT name,xuid,total_seconds,last_seen FROM players ORDER BY total_seconds DESC")
        return cur.fetchall()

    def daily_rollup(self, target: date, top_n: int):
        if not self.enabled():
            return (0,0,[])
        cur = self.cursor()
        if not cur:
            return (0,0,[])
        cur.execute("""
            SELECT COUNT(DISTINCT xuid), IFNULL(SUM(session_seconds),0)
            FROM sessions
            WHERE DATE(join_time)=%s
        """, (target,))
        unique_players, total_seconds = cur.fetchone()

        cur.execute("""
            INSERT IGNORE INTO daily_stats (stat_date, unique_players, total_seconds)
            VALUES (%s, %s, %s)
        """, (target, unique_players, total_seconds))

        cur.execute("""
            SELECT p.name, SUM(s.session_seconds) AS seconds
            FROM sessions s
            JOIN players p ON p.xuid = s.xuid
            WHERE DATE(s.join_time)=%s
            GROUP BY p.name
            ORDER BY seconds DESC
            LIMIT %s
        """, (target, top_n))
        top = cur.fetchall()
        return (unique_players, total_seconds, top)

mysqlm = MySQLManager(config)
try:
    mysqlm.ensure_tables()
except Exception:
    # We'll show a GUI warning later; keep app running for people without DB.
    pass

# ---------------------- BACKUP MANAGER ----------------------
class BackupManager:
    def __init__(self, cfg: configparser.ConfigParser, notify):
        self.cfg = cfg
        self.notify = notify
        self.lock = threading.Lock()

    def enabled(self) -> bool:
        return self.cfg["BACKUP"].getboolean("enabled", fallback=True)

    def backup_dir(self) -> str:
        p = self.cfg["BACKUP"].get("path", "backups").strip()
        if not os.path.isabs(p):
            p = os.path.join(BASE_DIR, p)
        return p

    def keep_last(self) -> int:
        try:
            return int(self.cfg["BACKUP"].get("keep_last", "20"))
        except ValueError:
            return 20

    def exclude_patterns(self):
        raw = self.cfg["BACKUP"].get("exclude_patterns", "").strip()
        if not raw:
            return []
        return [x.strip() for x in raw.split(";") if x.strip()]

    def should_exclude(self, filename: str) -> bool:
        for pat in self.exclude_patterns():
            if filename.endswith(pat) or pat in filename:
                return True
        return False

    def make_backup(self) -> str:
        with self.lock:
            os.makedirs(self.backup_dir(), exist_ok=True)
            name = datetime.now().strftime("backup_%Y-%m-%d_%H-%M-%S.zip")
            out_path = os.path.join(self.backup_dir(), name)

            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
                if self.cfg["BACKUP"].getboolean("include_worlds", fallback=True):
                    worlds_dir = os.path.join(BASE_DIR, "worlds")
                    if os.path.exists(worlds_dir):
                        for root, _, files in os.walk(worlds_dir):
                            for f in files:
                                if self.should_exclude(f):
                                    continue
                                full = os.path.join(root, f)
                                rel = os.path.relpath(full, BASE_DIR)
                                z.write(full, rel)

                if self.cfg["BACKUP"].getboolean("include_config", fallback=True):
                    if os.path.exists(CONFIG_FILE):
                        z.write(CONFIG_FILE, os.path.relpath(CONFIG_FILE, BASE_DIR))

            self.rotate()
            return out_path

    def rotate(self):
        keep = self.keep_last()
        files = []
        if os.path.exists(self.backup_dir()):
            for f in os.listdir(self.backup_dir()):
                if f.lower().endswith(".zip"):
                    files.append(os.path.join(self.backup_dir(), f))
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for f in files[keep:]:
            try:
                os.remove(f)
            except Exception:
                pass

backupm = BackupManager(config, discord.send_text)

# ---------------------- LOG PARSER + SERVER CONTROL ----------------------
@dataclass
class PlayerSession:
    name: str
    xuid: str
    joined_at: datetime

class ServerManager:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg
        self.proc = None
        self.thread = None
        self.running = False
        self.online = {}  # xuid -> PlayerSession
        self.last_event = {}  # xuid -> timestamp for anti-spam
        self.console_callbacks = []  # functions(line)
        self.status_callbacks = []   # functions(status_str)

        # multiple patterns (Bedrock versions can vary slightly)
        self.re_join = [
            re.compile(r"Player connected: ([^,]+), xuid: (\d+)", re.I),
            re.compile(r"Player connected: ([^,]+).*xuid:\s*(\d+)", re.I),
        ]
        self.re_leave = [
            re.compile(r"Player disconnected: ([^,]+), xuid: (\d+)", re.I),
            re.compile(r"Player disconnected: ([^,]+).*xuid:\s*(\d+)", re.I),
        ]
        self.re_started = re.compile(r"Server started\.", re.I)
        self.re_error = re.compile(r"\b(ERROR|FATAL)\b", re.I)

    def _workdir(self):
        wd = self.cfg["SERVER"].get("workdir", "").strip()
        if wd:
            return wd
        return BASE_DIR

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.is_running():
            return
        exe = self.cfg["SERVER"].get("exe_path", "bedrock_server.exe")
        if not os.path.isabs(exe):
            exe = os.path.join(self._workdir(), exe)
        if not os.path.exists(exe):
            raise FileNotFoundError(f"No se encontró: {exe}")

        enc = self.cfg["SERVER"].get("encoding", "utf-8")
        self.proc = subprocess.Popen(
            [exe],
            cwd=self._workdir(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding=enc,
            errors="ignore",
            bufsize=1
        )
        self.running = True
        self._status("RUNNING")
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def stop_soft(self):
        if not self.is_running():
            return
        try:
            self.proc.stdin.write("/stop\n")
            self.proc.stdin.flush()
        except Exception:
            pass

    def stop_hard(self):
        if not self.is_running():
            return
        hard_tree = self.cfg["SERVER"].getboolean("hard_kill_tree_windows", fallback=False)
        try:
            if os.name == "nt" and hard_tree:
                subprocess.call(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                self.proc.terminate()
        except Exception:
            pass

    def restart(self):
        if self.is_running():
            self.stop_soft()
            # give it a moment then hard if needed
            time.sleep(3)
            if self.is_running():
                self.stop_hard()
            time.sleep(2)
        self.start()

    def send_command(self, cmd: str):
        if not self.is_running():
            return
        try:
            if not cmd.endswith("\n"):
                cmd += "\n"
            self.proc.stdin.write(cmd)
            self.proc.stdin.flush()
        except Exception:
            pass

    def _anti_spam_ok(self, xuid: str) -> bool:
        try:
            gap = int(self.cfg["PARSER"].get("anti_spam_seconds", "5"))
        except ValueError:
            gap = 5
        now = time.time()
        last = self.last_event.get(xuid, 0.0)
        if now - last >= gap:
            self.last_event[xuid] = now
            return True
        return False

    def _emit_console(self, line: str):
        for cb in self.console_callbacks:
            try:
                cb(line)
            except Exception:
                pass

    def _status(self, s: str):
        for cb in self.status_callbacks:
            try:
                cb(s)
            except Exception:
                pass

    def _match_any(self, patterns, line):
        for p in patterns:
            m = p.search(line)
            if m:
                return m
        return None

    def _reader(self):
        # read lines until process ends
        try:
            for line in iter(self.proc.stdout.readline, ""):
                if not line:
                    break
                line = line.rstrip("\n")
                self._emit_console(line)
                self._handle_line(line)
        finally:
            self.running = False
            self._status("STOPPED")

    def _handle_line(self, line: str):
        # started
        if self.re_started.search(line):
            discord.send_text(config["MESSAGES"]["start"])
            return

        # join
        mj = self._match_any(self.re_join, line)
        if mj:
            name, xuid = mj.group(1).strip(), mj.group(2).strip()
            if self._anti_spam_ok(xuid):
                ts = datetime.now()
                self.online[xuid] = PlayerSession(name=name, xuid=xuid, joined_at=ts)
                try:
                    mysqlm.on_join(name, xuid, ts)
                except Exception:
                    pass
                msg = config["MESSAGES"]["join"].replace("{player}", name)
                discord.send_text(msg)
            return

        # leave
        ml = self._match_any(self.re_leave, line)
        if ml:
            name, xuid = ml.group(1).strip(), ml.group(2).strip()
            if self._anti_spam_ok(xuid):
                ts = datetime.now()
                sess = self.online.pop(xuid, None)
                secs = 0
                if sess:
                    secs = int((ts - sess.joined_at).total_seconds())
                try:
                    mysqlm.on_leave(xuid, ts, secs)
                except Exception:
                    pass
                msg = config["MESSAGES"]["leave"].replace("{player}", name)
                discord.send_text(msg)
            return

        # errors
        if config["PARSER"].getboolean("detect_errors", fallback=True) and self.re_error.search(line):
            msg = config["MESSAGES"]["error"].replace("{line}", line[:180])
            discord.send_text(msg)

serverm = ServerManager(config)

# ---------------------- DAILY SCHEDULER ----------------------
class DailyScheduler:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg
        self.thread = None
        self._last_run_date = None

    def enabled(self):
        return self.cfg["DAILY"].getboolean("enabled", fallback=True)

    def run_once(self, target_date: date):
        if not mysqlm.enabled():
            return
        top_n = int(self.cfg["DAILY"].get("top_n", "5"))
        unique_players, total_seconds, top = mysqlm.daily_rollup(target_date, top_n)

        def fmt(sec: int) -> str:
            h = sec // 3600
            m = (sec % 3600) // 60
            return f"{h}h {m}m"

        top_text = "\n".join(
            f"**{i+1}. {name}** — {fmt(int(sec))}"
            for i, (name, sec) in enumerate(top)
        ) or "Sin actividad"

        title = config["MESSAGES"]["daily_title"].replace("{date}", str(target_date))
        discord.send_embed(
            title,
            [
                {"name": "👥 Jugadores únicos", "value": str(unique_players), "inline": True},
                {"name": "⏱️ Tiempo total", "value": fmt(int(total_seconds)), "inline": True},
                {"name": "🏆 Top jugadores", "value": top_text, "inline": False},
            ]
        )

    def loop(self):
        while True:
            if not self.enabled():
                time.sleep(5)
                continue
            now = datetime.now()
            hour = int(self.cfg["DAILY"].get("hour", "0"))
            minute = int(self.cfg["DAILY"].get("minute", "5"))

            # run exactly once per day, after schedule time
            if now.hour == hour and now.minute == minute:
                if self._last_run_date != now.date():
                    # by default report yesterday
                    self.run_once(date.today() - timedelta(days=1))
                    self._last_run_date = now.date()
                time.sleep(60)
            time.sleep(10)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

dailys = DailyScheduler(config)

# ---------------------- BACKUP SCHEDULER ----------------------
class BackupScheduler:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg
        self.thread = None

    def enabled(self):
        return self.cfg["BACKUP"].getboolean("enabled", fallback=True)

    def loop(self):
        while True:
            if self.enabled():
                try:
                    out = backupm.make_backup()
                    fname = os.path.basename(out)
                    discord.send_text(config["MESSAGES"]["backup_done"].replace("{file}", fname))
                except Exception:
                    pass
            try:
                mins = int(self.cfg["BACKUP"].get("interval_minutes", "60"))
            except ValueError:
                mins = 60
            time.sleep(max(1, mins) * 60)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

backups = BackupScheduler(config)

# ---------------------- GUI ----------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LatinBat Bedrock Manager v2")
        self.geometry("980x680")
        self.minsize(900, 600)

        self.status_var = tk.StringVar(value="STOPPED")
        self.online_var = tk.StringVar(value="Online: 0")
        self.db_var = tk.StringVar(value="DB: ?")
        self.webhook_var = tk.StringVar(value="")
        self.server_path_var = tk.StringVar(value=config["SERVER"]["exe_path"])

        self._build_ui()
        self._wire_callbacks()

        # init displays
        self.refresh_players_table()
        self.update_status_labels()

        # start schedulers
        dailys.start()
        backups.start()

        # try show DB state
        self._set_db_state()

    def _build_ui(self):
        # header status
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)

        ttk.Label(top, text="Estado:").pack(side="left")
        ttk.Label(top, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).pack(side="left", padx=(6, 18))
        ttk.Label(top, textvariable=self.online_var).pack(side="left")
        ttk.Label(top, textvariable=self.db_var).pack(side="left", padx=(18, 0))

        btns = ttk.Frame(top)
        btns.pack(side="right")

        ttk.Button(btns, text="▶ Iniciar", command=self.on_start).pack(side="left", padx=4)
        ttk.Button(btns, text="🛑 /stop", command=self.on_stop_soft).pack(side="left", padx=4)
        ttk.Button(btns, text="⛔ Stop", command=self.on_stop_hard).pack(side="left", padx=4)
        ttk.Button(btns, text="🔁 Reiniciar", command=self.on_restart).pack(side="left", padx=4)

        # tabs
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=8)

        self.tab_console = ttk.Frame(self.nb)
        self.tab_stats = ttk.Frame(self.nb)
        self.tab_discord = ttk.Frame(self.nb)
        self.tab_backup = ttk.Frame(self.nb)
        self.tab_settings = ttk.Frame(self.nb)

        self.nb.add(self.tab_console, text="🖥️ Consola")
        self.nb.add(self.tab_stats, text="📊 Estadísticas")
        self.nb.add(self.tab_discord, text="🔔 Discord")
        self.nb.add(self.tab_backup, text="📦 Backups")
        self.nb.add(self.tab_settings, text="⚙️ Ajustes")

        # console tab
        c_top = ttk.Frame(self.tab_console)
        c_top.pack(fill="x", padx=8, pady=8)
        ttk.Label(c_top, text="Enviar comando:").pack(side="left")
        self.cmd_entry = ttk.Entry(c_top)
        self.cmd_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(c_top, text="Enviar", command=self.on_send_cmd).pack(side="left")
        ttk.Button(c_top, text="Limpiar", command=self.on_clear_console).pack(side="left", padx=6)

        self.console = tk.Text(self.tab_console, height=20, wrap="word")
        self.console.pack(fill="both", expand=True, padx=8, pady=(0,8))
        self.console.configure(state="disabled")

        # stats tab
        st_top = ttk.Frame(self.tab_stats)
        st_top.pack(fill="x", padx=8, pady=8)
        ttk.Button(st_top, text="🔄 Refrescar", command=self.refresh_players_table).pack(side="left")
        ttk.Button(st_top, text="📊 Ejecutar resumen diario ahora", command=self.on_run_daily_now).pack(side="left", padx=8)

        self.tree = ttk.Treeview(self.tab_stats, columns=("name","xuid","hours","last_seen"), show="headings")
        for col, title, w in [
            ("name","Jugador",220),
            ("xuid","XUID",260),
            ("hours","Horas",80),
            ("last_seen","Última vez",180)
        ]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0,8))

        # discord tab (webhook + messages + embeds)
        d_wrap = ttk.Frame(self.tab_discord)
        d_wrap.pack(fill="both", expand=True, padx=8, pady=8)

        # webhook frame
        wh = ttk.LabelFrame(d_wrap, text="🔐 Webhook (se guarda protegido en config.ini)")
        wh.pack(fill="x", padx=6, pady=6)

        ttk.Label(wh, text="Webhook URL:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.webhook_entry = ttk.Entry(wh)
        self.webhook_entry.grid(row=0, column=1, sticky="we", padx=6, pady=6)
        wh.columnconfigure(1, weight=1)

        ttk.Button(wh, text="Guardar webhook", command=self.on_save_webhook).grid(row=0, column=2, padx=6, pady=6)
        ttk.Button(wh, text="Probar", command=self.on_test_webhook).grid(row=0, column=3, padx=6, pady=6)

        # messages editor
        msgf = ttk.LabelFrame(d_wrap, text="🎨 Editor de mensajes (usa {player} y {date})")
        msgf.pack(fill="both", expand=True, padx=6, pady=6)

        self.msg_vars = {}
        msg_keys = ["join","leave","start","stop_soft","stop_hard","backup_done","error","daily_title"]
        labels = {
            "join":"Join", "leave":"Leave", "start":"Start", "stop_soft":"Stop (/stop)",
            "stop_hard":"Stop (hard)", "backup_done":"Backup", "error":"Error", "daily_title":"Daily title"
        }
        for r, k in enumerate(msg_keys):
            self.msg_vars[k] = tk.StringVar(value=config["MESSAGES"].get(k, DEFAULTS["MESSAGES"][k]))
            ttk.Label(msgf, text=labels[k]).grid(row=r, column=0, sticky="w", padx=6, pady=4)
            e = ttk.Entry(msgf, textvariable=self.msg_vars[k])
            e.grid(row=r, column=1, sticky="we", padx=6, pady=4)
        msgf.columnconfigure(1, weight=1)

        btn_line = ttk.Frame(msgf)
        btn_line.grid(row=len(msg_keys), column=0, columnspan=2, sticky="we", padx=6, pady=6)
        ttk.Button(btn_line, text="💾 Guardar mensajes", command=self.on_save_messages).pack(side="left")
        ttk.Button(btn_line, text="👁️ Preview", command=self.on_preview_messages).pack(side="left", padx=6)

        # embed config
        emb = ttk.LabelFrame(d_wrap, text="🧩 Embeds")
        emb.pack(fill="x", padx=6, pady=6)

        self.embed_enabled = tk.BooleanVar(value=config["EMBED"].getboolean("enabled", fallback=True))
        self.embed_color = tk.StringVar(value=config["EMBED"].get("color","3447003"))
        self.embed_footer = tk.StringVar(value=config["EMBED"].get("footer","LatinBat Bedrock Server"))
        self.discord_enabled = tk.BooleanVar(value=config["DISCORD"].getboolean("enabled", fallback=True))
        self.discord_mode = tk.StringVar(value=config["DISCORD"].get("mode","embed"))
        self.discord_username = tk.StringVar(value=config["DISCORD"].get("username","LatinBat Bot"))
        self.discord_avatar = tk.StringVar(value=config["DISCORD"].get("avatar_url",""))

        ttk.Checkbutton(emb, text="Habilitar Discord", variable=self.discord_enabled).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(emb, text="Embeds habilitados", variable=self.embed_enabled).grid(row=0, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(emb, text="Modo").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(emb, textvariable=self.discord_mode, values=["embed","text"], state="readonly", width=10).grid(row=1, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(emb, text="Color (int)").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(emb, textvariable=self.embed_color, width=14).grid(row=2, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(emb, text="Footer").grid(row=3, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(emb, textvariable=self.embed_footer, width=40).grid(row=3, column=1, sticky="we", padx=6, pady=4)

        ttk.Label(emb, text="Username").grid(row=4, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(emb, textvariable=self.discord_username, width=30).grid(row=4, column=1, sticky="we", padx=6, pady=4)

        ttk.Label(emb, text="Avatar URL").grid(row=5, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(emb, textvariable=self.discord_avatar, width=50).grid(row=5, column=1, sticky="we", padx=6, pady=4)

        ttk.Button(emb, text="💾 Guardar Discord/Embeds", command=self.on_save_discord_embed).grid(row=6, column=1, sticky="e", padx=6, pady=6)
        emb.columnconfigure(1, weight=1)

        # backup tab
        b = ttk.Frame(self.tab_backup)
        b.pack(fill="both", expand=True, padx=8, pady=8)
        self.backup_enabled = tk.BooleanVar(value=config["BACKUP"].getboolean("enabled", fallback=True))
        self.backup_interval = tk.StringVar(value=config["BACKUP"].get("interval_minutes","60"))
        self.backup_path = tk.StringVar(value=config["BACKUP"].get("path","backups"))
        self.backup_keep = tk.StringVar(value=config["BACKUP"].get("keep_last","20"))
        self.backup_excl = tk.StringVar(value=config["BACKUP"].get("exclude_patterns",".tmp;.lock"))

        bf = ttk.LabelFrame(b, text="Configuración de backups")
        bf.pack(fill="x", padx=6, pady=6)

        ttk.Checkbutton(bf, text="Habilitar backups automáticos", variable=self.backup_enabled).grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Label(bf, text="Intervalo (min)").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(bf, textvariable=self.backup_interval, width=10).grid(row=1, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(bf, text="Guardar en").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(bf, textvariable=self.backup_path, width=30).grid(row=2, column=1, sticky="we", padx=6, pady=4)
        ttk.Button(bf, text="Elegir", command=self.on_pick_backup_dir).grid(row=2, column=2, padx=6, pady=4)

        ttk.Label(bf, text="Mantener últimos").grid(row=3, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(bf, textvariable=self.backup_keep, width=10).grid(row=3, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(bf, text="Exclusiones (;)").grid(row=4, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(bf, textvariable=self.backup_excl, width=40).grid(row=4, column=1, sticky="we", padx=6, pady=4)

        ttk.Button(bf, text="💾 Guardar backups", command=self.on_save_backup).grid(row=5, column=1, sticky="e", padx=6, pady=6)
        ttk.Button(bf, text="📦 Backup ahora", command=self.on_backup_now).grid(row=5, column=2, sticky="e", padx=6, pady=6)
        bf.columnconfigure(1, weight=1)

        # settings tab
        s = ttk.Frame(self.tab_settings)
        s.pack(fill="both", expand=True, padx=8, pady=8)

        sf = ttk.LabelFrame(s, text="Servidor")
        sf.pack(fill="x", padx=6, pady=6)

        ttk.Label(sf, text="bedrock_server.exe").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(sf, textvariable=self.server_path_var, width=50).grid(row=0, column=1, sticky="we", padx=6, pady=6)
        ttk.Button(sf, text="Buscar", command=self.on_pick_server_exe).grid(row=0, column=2, padx=6, pady=6)
        sf.columnconfigure(1, weight=1)

        df = ttk.LabelFrame(s, text="Scheduler diario")
        df.pack(fill="x", padx=6, pady=6)

        self.daily_enabled = tk.BooleanVar(value=config["DAILY"].getboolean("enabled", fallback=True))
        self.daily_hour = tk.StringVar(value=config["DAILY"].get("hour","0"))
        self.daily_min = tk.StringVar(value=config["DAILY"].get("minute","5"))
        self.daily_top = tk.StringVar(value=config["DAILY"].get("top_n","5"))

        ttk.Checkbutton(df, text="Habilitar resumen diario", variable=self.daily_enabled).grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Label(df, text="Hora").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(df, textvariable=self.daily_hour, width=6).grid(row=1, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(df, text="Min").grid(row=1, column=2, sticky="w", padx=6, pady=4)
        ttk.Entry(df, textvariable=self.daily_min, width=6).grid(row=1, column=3, sticky="w", padx=6, pady=4)
        ttk.Label(df, text="Top N").grid(row=1, column=4, sticky="w", padx=6, pady=4)
        ttk.Entry(df, textvariable=self.daily_top, width=6).grid(row=1, column=5, sticky="w", padx=6, pady=4)
        ttk.Button(df, text="💾 Guardar scheduler", command=self.on_save_daily).grid(row=2, column=5, sticky="e", padx=6, pady=6)

    def _wire_callbacks(self):
        serverm.console_callbacks.append(self.on_console_line)
        serverm.status_callbacks.append(self.on_status_change)

    # ---- UI helpers ----
    def log_to_console(self, text: str):
        self.console.configure(state="normal")
        self.console.insert("end", text + "\n")
        self.console.see("end")
        self.console.configure(state="disabled")

    def on_console_line(self, line: str):
        # called from background thread -> use after()
        self.after(0, lambda: self.log_to_console(line))

    def on_status_change(self, status: str):
        self.after(0, lambda: self.status_var.set(status))
        self.after(0, self.update_status_labels)

    def update_status_labels(self):
        self.online_var.set(f"Online: {len(serverm.online)}")
        self._set_db_state()

    def _set_db_state(self):
        if mysqlm.enabled():
            try:
                mysqlm.connect()
                self.db_var.set("DB: OK")
            except Exception:
                self.db_var.set("DB: ERROR")
        else:
            self.db_var.set("DB: OFF")

    # ---- server controls ----
    def on_start(self):
        try:
            serverm.start()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def on_stop_soft(self):
        serverm.stop_soft()
        discord.send_text(config["MESSAGES"]["stop_soft"])

    def on_stop_hard(self):
        serverm.stop_hard()
        discord.send_text(config["MESSAGES"]["stop_hard"])

    def on_restart(self):
        try:
            serverm.restart()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def on_send_cmd(self):
        cmd = self.cmd_entry.get().strip()
        if cmd:
            serverm.send_command(cmd)
            self.cmd_entry.delete(0, "end")

    def on_clear_console(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    # ---- stats ----
    def refresh_players_table(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        rows = []
        try:
            rows = mysqlm.fetch_players()
        except Exception:
            rows = []
        for name, xuid, total_seconds, last_seen in rows:
            hours = round((int(total_seconds) / 3600.0), 2)
            self.tree.insert("", "end", values=(name, xuid, hours, str(last_seen)))
        self.update_status_labels()

    def on_run_daily_now(self):
        # run for yesterday by default
        try:
            d = date.today() - timedelta(days=1)
            top_n = int(config["DAILY"].get("top_n", "5"))
            unique_players, total_seconds, top = mysqlm.daily_rollup(d, top_n)

            def fmt(sec: int) -> str:
                h = sec // 3600
                m = (sec % 3600) // 60
                return f"{h}h {m}m"

            top_text = "\n".join(
                f"**{i+1}. {name}** — {fmt(int(sec))}"
                for i, (name, sec) in enumerate(top)
            ) or "Sin actividad"

            title = config["MESSAGES"]["daily_title"].replace("{date}", str(d))
            discord.send_embed(
                title,
                [
                    {"name": "👥 Jugadores únicos", "value": str(unique_players), "inline": True},
                    {"name": "⏱️ Tiempo total", "value": fmt(int(total_seconds)), "inline": True},
                    {"name": "🏆 Top jugadores", "value": top_text, "inline": False},
                ]
            )
            messagebox.showinfo("OK", "Resumen diario enviado a Discord.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ---- discord/webhook ----
    def on_save_webhook(self):
        url = self.webhook_entry.get().strip()
        discord.set_webhook_url(url)
        save_config()
        messagebox.showinfo("OK", "Webhook guardado (protegido).")

    def on_test_webhook(self):
        discord.send_text("✅ Webhook OK - mensaje de prueba (LatinBat Bedrock Manager)")
        messagebox.showinfo("OK", "Mensaje de prueba enviado (si el webhook es correcto).")

    def on_save_messages(self):
        for k, var in self.msg_vars.items():
            config["MESSAGES"][k] = var.get()
        save_config()
        messagebox.showinfo("OK", "Mensajes guardados.")

    def on_preview_messages(self):
        sample_player = "Steve"
        sample_date = str(date.today())
        preview = []
        for k in ["join","leave","start","stop_soft","stop_hard","backup_done","error","daily_title"]:
            t = self.msg_vars[k].get()
            t = t.replace("{player}", sample_player).replace("{date}", sample_date).replace("{file}", "backup_test.zip").replace("{line}", "ERROR demo")
            preview.append(f"{k}: {t}")
        messagebox.showinfo("Preview", "\n\n".join(preview))

    def on_save_discord_embed(self):
        config["DISCORD"]["enabled"] = "true" if self.discord_enabled.get() else "false"
        config["DISCORD"]["mode"] = self.discord_mode.get()
        config["DISCORD"]["username"] = self.discord_username.get().strip()
        config["DISCORD"]["avatar_url"] = self.discord_avatar.get().strip()

        config["EMBED"]["enabled"] = "true" if self.embed_enabled.get() else "false"
        config["EMBED"]["color"] = self.embed_color.get().strip() or "3447003"
        config["EMBED"]["footer"] = self.embed_footer.get().strip() or "LatinBat Bedrock Server"

        save_config()
        messagebox.showinfo("OK", "Discord/Embeds guardados.")

    # ---- backups ----
    def on_pick_backup_dir(self):
        d = filedialog.askdirectory(initialdir=BASE_DIR)
        if d:
            # store relative if inside BASE_DIR
            try:
                rel = os.path.relpath(d, BASE_DIR)
                if not rel.startswith(".."):
                    self.backup_path.set(rel)
                else:
                    self.backup_path.set(d)
            except Exception:
                self.backup_path.set(d)

    def on_save_backup(self):
        config["BACKUP"]["enabled"] = "true" if self.backup_enabled.get() else "false"
        config["BACKUP"]["interval_minutes"] = self.backup_interval.get().strip() or "60"
        config["BACKUP"]["path"] = self.backup_path.get().strip() or "backups"
        config["BACKUP"]["keep_last"] = self.backup_keep.get().strip() or "20"
        config["BACKUP"]["exclude_patterns"] = self.backup_excl.get().strip()
        save_config()
        messagebox.showinfo("OK", "Backups guardados.")

    def on_backup_now(self):
        try:
            out = backupm.make_backup()
            fname = os.path.basename(out)
            discord.send_text(config["MESSAGES"]["backup_done"].replace("{file}", fname))
            messagebox.showinfo("OK", f"Backup creado:\n{out}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ---- settings ----
    def on_pick_server_exe(self):
        p = filedialog.askopenfilename(
            initialdir=BASE_DIR,
            title="Selecciona bedrock_server.exe",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")]
        )
        if p:
            # save relative if possible
            try:
                rel = os.path.relpath(p, BASE_DIR)
                if not rel.startswith(".."):
                    self.server_path_var.set(rel)
                else:
                    self.server_path_var.set(p)
            except Exception:
                self.server_path_var.set(p)
            config["SERVER"]["exe_path"] = self.server_path_var.get()
            save_config()

    def on_save_daily(self):
        config["DAILY"]["enabled"] = "true" if self.daily_enabled.get() else "false"
        config["DAILY"]["hour"] = self.daily_hour.get().strip() or "0"
        config["DAILY"]["minute"] = self.daily_min.get().strip() or "5"
        config["DAILY"]["top_n"] = self.daily_top.get().strip() or "5"
        save_config()
        messagebox.showinfo("OK", "Scheduler diario guardado.")

# ---------------------- APP START ----------------------
def prefill_webhook_entry(app: App):
    # show decrypted url in UI (still saved protected)
    url = discord.webhook_url()
    app.webhook_entry.delete(0, "end")
    app.webhook_entry.insert(0, url)

def main():
    app = App()
    prefill_webhook_entry(app)
    app.mainloop()

if __name__ == "__main__":
    main()
