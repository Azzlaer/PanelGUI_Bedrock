import os
import sys
import re
import time
import csv
import base64
import zipfile
import threading
import subprocess
import configparser
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Optional, Callable, Dict, List, Tuple

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# optional deps
try:
    import requests
except Exception:
    requests = None

try:
    import mysql.connector
except Exception:
    mysql = None
    mysql_connector = None
else:
    mysql_connector = mysql.connector

# ---------------------- PATHS ----------------------
def app_dir() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = app_dir()
CONFIG_FILE = os.path.join(BASE_DIR, "config.ini")

# ---------------------- SECURITY (Webhook tokenization) ----------------------
def machine_salt() -> str:
    # Best-effort stable salt; changes across machines/users (good for protection)
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
        return ""

# ---------------------- CONFIG DEFAULTS ----------------------
DEFAULTS_GLOBAL = {
    "GLOBAL": {
        "active_profile": "default",
        "profiles": "default",
    },
    "DISCORD": {
        "webhook_token": "",
        "webhook_key_hint": "LatinBatKey",
        "enabled": "true",
        "mode": "embed",               # embed|text
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
        "backup_fail": "⚠️ Backup falló: {reason}",
        "error": "🚨 Error detectado: {line}",
        "watchdog_restart": "♻️ Watchdog: reiniciando servidor ({reason})",
        "watchdog_giveup": "🧯 Watchdog: demasiados fallos, deteniendo auto-reinicio",
        "daily_title": "📊 Estadísticas diarias ({date})",
        "restart_warn_5m": "⚠️ EL SERVIDOR SE REINICIARÁ EN 5 MINUTOS ⚠️",
        "restart_now": "♻️ Reinicio en curso...",
        "maintenance_on": "🔒 Modo mantenimiento activado. Reinicio pronto.",
        "maintenance_off": "✅ Modo mantenimiento desactivado. ¡Bienvenidos!",
        "motd_prefix": "📢 "
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
    "DAILY": {
        "enabled": "true",
        "hour": "0",
        "minute": "5",
        "top_n": "5"
    }
}

DEFAULTS_PROFILE = {
    "SERVER": {
        "exe_path": "bedrock_server.exe",
        "workdir": "",                      # empty means BASE_DIR
        "hard_kill_tree_windows": "false",
        "encoding": "utf-8",
        "ingame_say_command": "say",         # say|tellraw
        "tellraw_format": r'{"rawtext":[{"text":"{msg}"}]}'
    },
    "PARSER": {
        "anti_spam_seconds": "5",
        "detect_errors": "true",
    },
    "BACKUP": {
        "enabled": "true",
        "interval_minutes": "60",
        "path": "backups",
        "keep_last": "20",
        "include_worlds": "true",
        "include_config": "true",
        "exclude_patterns": ".tmp;.lock",
    },
    "WATCHDOG": {
        "enabled": "true",
        "hang_minutes": "12",               # no console activity => restart
        "max_restarts_per_hour": "5",
        "backoff_seconds": "20",            # base backoff, grows exponentially
        "require_started_line": "true",      # wait for "Server started." confirmation
        "startup_timeout_seconds": "120",    # if not started => restart
        "lag_warn_only": "true"              # if hang detected: warn only or restart (if false)
    },
    "AUTOFIX": {
        "enabled": "true",
        "grace_minutes": "10",
        "interval_minutes": "5",
        "notify_discord": "true"
    },
    "MOTD": {
        "enabled": "false",
        "interval_minutes": "15",
        "only_if_players_online": "true",
        "use_prefix": "true",
        "messages": "Reglas: Respeta a todos|Discord: https://discord.gg/tu-link|Web: https://latinbattle.com"
    },
    "MAINTENANCE": {
        "enabled": "true",
        "use_whitelist": "true",
        "kick_before_restart": "true",
        "kick_message": "Reinicio programado. Vuelve en 2-3 minutos.",
    },
    "RESTART": {
        "enabled": "false",
        "mode": "interval",                # interval|daily
        "interval_hours": "6",
        "daily_time": "04:00",            # HH:MM
        "warn_minutes": "5",
        "announce_discord": "true"
    }
}

def sec(profile: str, section: str) -> str:
    return f"PROFILE:{profile}:{section}"

def ensure_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not os.path.exists(CONFIG_FILE):
        # Create defaults with one profile
        for section, kv in DEFAULTS_GLOBAL.items():
            cfg[section] = dict(kv)
        prefix = "PROFILE:default:"
        for s, kv in DEFAULTS_PROFILE.items():
            cfg[prefix + s] = dict(kv)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            cfg.write(f)

    cfg.read(CONFIG_FILE, encoding="utf-8")

    changed = False
    # Ensure global sections/keys
    for section, kv in DEFAULTS_GLOBAL.items():
        if section not in cfg:
            cfg[section] = {}
            changed = True
        for k, v in kv.items():
            if k not in cfg[section]:
                cfg[section][k] = v
                changed = True

    # Ensure profiles list
    profiles = [p.strip() for p in cfg["GLOBAL"].get("profiles", "default").split(",") if p.strip()]
    if not profiles:
        profiles = ["default"]
        cfg["GLOBAL"]["profiles"] = "default"
        cfg["GLOBAL"]["active_profile"] = "default"
        changed = True

    active = cfg["GLOBAL"].get("active_profile", profiles[0]).strip()
    if active not in profiles:
        cfg["GLOBAL"]["active_profile"] = profiles[0]
        changed = True

    # Ensure profile sections/keys
    for p in profiles:
        for s, kv in DEFAULTS_PROFILE.items():
            sname = sec(p, s)
            if sname not in cfg:
                cfg[sname] = {}
                changed = True
            for k, v in kv.items():
                if k not in cfg[sname]:
                    cfg[sname][k] = v
                    changed = True

    if changed:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            cfg.write(f)

    return cfg

config = ensure_config()

def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        config.write(f)

def get_profiles() -> List[str]:
    return [p.strip() for p in config["GLOBAL"].get("profiles", "default").split(",") if p.strip()]

def active_profile() -> str:
    return config["GLOBAL"].get("active_profile", "default").strip()

def set_active_profile(name: str):
    config["GLOBAL"]["active_profile"] = name
    save_config()

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
        return self.cfg["DISCORD"].getboolean("enabled", fallback=True) and requests is not None

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
        if not self.enabled() or not self._rate_limit_ok():
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
        # fallback to text
        if (not self.cfg["EMBED"].getboolean("enabled", fallback=True)) or (self.cfg["DISCORD"].get("mode","embed") != "embed"):
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
        return self.cfg["MYSQL"].getboolean("enabled", fallback=True) and mysql_connector is not None

    def connect(self):
        if not self.enabled():
            return
        with self.lock:
            if self.conn and self.conn.is_connected():
                return
            self.conn = mysql_connector.connect(
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
          fix_reason VARCHAR(64) NULL,
          INDEX (xuid),
          INDEX (join_time),
          INDEX (leave_time),
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
        cur.execute("""
        CREATE TABLE IF NOT EXISTS server_events (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          profile VARCHAR(64) NOT NULL,
          event_type VARCHAR(32) NOT NULL,
          reason VARCHAR(255) NULL,
          uptime_seconds BIGINT NOT NULL DEFAULT 0,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          INDEX(profile),
          INDEX(event_type),
          INDEX(created_at)
        )
        """)

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

    def fetch_players(self) -> List[Tuple]:
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

    def insert_event(self, profile: str, event_type: str, reason: str, uptime_seconds: int):
        if not self.enabled():
            return
        cur = self.cursor()
        if not cur:
            return
        cur.execute("""
            INSERT INTO server_events (profile, event_type, reason, uptime_seconds)
            VALUES (%s, %s, %s, %s)
        """, (profile, event_type, reason[:255] if reason else None, int(uptime_seconds)))

    def close_hung_sessions(self, grace_minutes: int, fix_reason: str = "auto_fix_crash") -> int:
        if not self.enabled():
            return 0
        cur = self.cursor()
        if not cur:
            return 0
        # Close sessions older than NOW - grace
        cur.execute("""
            SELECT id, xuid, join_time
            FROM sessions
            WHERE leave_time IS NULL
              AND join_time < (NOW() - INTERVAL %s MINUTE)
        """, (int(grace_minutes),))
        rows = cur.fetchall()
        fixed = 0
        for sid, xuid, join_time in rows:
            # compute seconds from join_time to now
            cur.execute("SELECT TIMESTAMPDIFF(SECOND, %s, NOW())", (join_time,))
            (secs,) = cur.fetchone()
            if secs is None:
                secs = 0
            cur.execute("""
                UPDATE sessions
                SET leave_time = NOW(),
                    session_seconds = %s,
                    fix_reason = %s
                WHERE id = %s
            """, (int(secs), fix_reason, sid))
            cur.execute("""
                UPDATE players
                SET total_seconds = total_seconds + %s,
                    last_seen = NOW()
                WHERE xuid = %s
            """, (int(secs), xuid))
            fixed += 1
        return fixed

mysqlm = MySQLManager(config)
try:
    mysqlm.ensure_tables()
except Exception:
    pass

# ---------------------- BACKUP MANAGER ----------------------
class BackupManager:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg
        self.lock = threading.Lock()

    def enabled(self, profile: str) -> bool:
        return self.cfg[sec(profile,"BACKUP")].getboolean("enabled", fallback=True)

    def backup_dir(self, profile: str) -> str:
        p = self.cfg[sec(profile,"BACKUP")].get("path", "backups").strip()
        if not os.path.isabs(p):
            p = os.path.join(BASE_DIR, p)
        return os.path.join(p, profile)

    def keep_last(self, profile: str) -> int:
        try:
            return int(self.cfg[sec(profile,"BACKUP")].get("keep_last", "20"))
        except ValueError:
            return 20

    def exclude_patterns(self, profile: str):
        raw = self.cfg[sec(profile,"BACKUP")].get("exclude_patterns", "").strip()
        if not raw:
            return []
        return [x.strip() for x in raw.split(";") if x.strip()]

    def should_exclude(self, profile: str, filename: str) -> bool:
        for pat in self.exclude_patterns(profile):
            if filename.endswith(pat) or pat in filename:
                return True
        return False

    def make_backup(self, profile: str) -> str:
        with self.lock:
            outdir = self.backup_dir(profile)
            os.makedirs(outdir, exist_ok=True)
            name = datetime.now().strftime("backup_%Y-%m-%d_%H-%M-%S.zip")
            out_path = os.path.join(outdir, name)

            wd = config[sec(profile,"SERVER")].get("workdir","").strip() or BASE_DIR
            worlds_dir = os.path.join(wd, "worlds")

            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
                if self.cfg[sec(profile,"BACKUP")].getboolean("include_worlds", fallback=True):
                    if os.path.exists(worlds_dir):
                        for root, _, files in os.walk(worlds_dir):
                            for f in files:
                                if self.should_exclude(profile, f):
                                    continue
                                full = os.path.join(root, f)
                                rel = os.path.relpath(full, wd)
                                z.write(full, rel)

                if self.cfg[sec(profile,"BACKUP")].getboolean("include_config", fallback=True):
                    if os.path.exists(CONFIG_FILE):
                        z.write(CONFIG_FILE, os.path.relpath(CONFIG_FILE, BASE_DIR))

            self.rotate(profile)
            return out_path

    def rotate(self, profile: str):
        keep = self.keep_last(profile)
        outdir = self.backup_dir(profile)
        files = []
        if os.path.exists(outdir):
            for f in os.listdir(outdir):
                if f.lower().endswith(".zip"):
                    files.append(os.path.join(outdir, f))
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for f in files[keep:]:
            try:
                os.remove(f)
            except Exception:
                pass

backupm = BackupManager(config)

# ---------------------- SERVER MANAGER + PARSER ----------------------
@dataclass
class PlayerSession:
    name: str
    xuid: str
    joined_at: datetime

class Metrics:
    def __init__(self):
        self.start_time: Optional[datetime] = None
        self.restarts_total = 0
        self.backups_ok = 0
        self.backups_fail = 0
        self.last_backup_file = ""
        self.last_error_line = ""
        self.last_restart_reason = ""
        self.last_hang_at: Optional[datetime] = None
        self.last_console_activity: Optional[datetime] = None
        self.watchdog_disabled_until: Optional[datetime] = None

    def uptime_seconds(self) -> int:
        if not self.start_time:
            return 0
        return int((datetime.now() - self.start_time).total_seconds())

    def uptime_str(self) -> str:
        secs = self.uptime_seconds()
        h = secs // 3600
        m = (secs % 3600) // 60
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"

class ServerManager:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg
        self.proc: Optional[subprocess.Popen] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.online: Dict[str, PlayerSession] = {}
        self.last_event: Dict[str, float] = {}
        self.console_callbacks: List[Callable[[str], None]] = []
        self.status_callbacks: List[Callable[[str], None]] = []
        self.profile = active_profile()
        self.metrics = Metrics()

        # patterns
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

        self._status("STOPPED")

    def set_profile(self, profile: str):
        self.profile = profile

    def _sec(self, section: str) -> str:
        return sec(self.profile, section)

    def _workdir(self) -> str:
        wd = self.cfg[self._sec("SERVER")].get("workdir","").strip()
        return wd or BASE_DIR

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _emit_console(self, line: str):
        self.metrics.last_console_activity = datetime.now()
        for cb in self.console_callbacks:
            try: cb(line)
            except Exception: pass

    def _status(self, s: str):
        for cb in self.status_callbacks:
            try: cb(s)
            except Exception: pass

    def _anti_spam_ok(self, xuid: str) -> bool:
        try:
            gap = int(self.cfg[self._sec("PARSER")].get("anti_spam_seconds", "5"))
        except ValueError:
            gap = 5
        now = time.time()
        last = self.last_event.get(xuid, 0.0)
        if now - last >= gap:
            self.last_event[xuid] = now
            return True
        return False

    def start(self):
        if self.is_running():
            return
        exe = self.cfg[self._sec("SERVER")].get("exe_path", "bedrock_server.exe")
        if not os.path.isabs(exe):
            exe = os.path.join(self._workdir(), exe)
        if not os.path.exists(exe):
            raise FileNotFoundError(f"No se encontró: {exe}")

        enc = self.cfg[self._sec("SERVER")].get("encoding", "utf-8")

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
        self.metrics.start_time = datetime.now()
        self.metrics.last_console_activity = datetime.now()
        self._status("RUNNING")

        # record event
        try:
            mysqlm.insert_event(self.profile, "start", "manual/start", 0)
        except Exception:
            pass

        self.reader_thread = threading.Thread(target=self._reader, daemon=True)
        self.reader_thread.start()

    def stop_soft(self):
        if not self.is_running():
            return
        try:
            self.proc.stdin.write("stop\n")
            self.proc.stdin.flush()
        except Exception:
            pass

    def stop_hard(self):
        if not self.is_running():
            return
        hard_tree = self.cfg[self._sec("SERVER")].getboolean("hard_kill_tree_windows", fallback=False)
        try:
            if os.name == "nt" and hard_tree:
                subprocess.call(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                self.proc.terminate()
        except Exception:
            pass

    def restart(self, reason: str = "manual"):
        self.metrics.last_restart_reason = reason
        self.metrics.restarts_total += 1

        # record event (with current uptime)
        try:
            mysqlm.insert_event(self.profile, "restart", reason, self.metrics.uptime_seconds())
        except Exception:
            pass

        if self.is_running():
            self.stop_soft()
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

    def say_ingame(self, msg: str):
        # Bedrock: `say <msg>` works. `tellraw` also works.
        mode = self.cfg[self._sec("SERVER")].get("ingame_say_command", "say").strip().lower()
        if mode == "tellraw":
            fmt = self.cfg[self._sec("SERVER")].get("tellraw_format", r'{"rawtext":[{"text":"{msg}"}]}')
            payload = fmt.replace("{msg}", msg.replace('"', r'\"'))
            self.send_command(f"tellraw @a {payload}")
        else:
            # default say
            self.send_command(f"say {msg}")

    def _match_any(self, patterns, line):
        for p in patterns:
            m = p.search(line)
            if m:
                return m
        return None

    def _reader(self):
        try:
            for line in iter(self.proc.stdout.readline, ""):
                if not line:
                    break
                line = line.rstrip("\n")
                self._emit_console(line)
                self._handle_line(line)
        finally:
            # process ended
            self._status("STOPPED")
            try:
                mysqlm.insert_event(self.profile, "stop", "process_exit", self.metrics.uptime_seconds())
            except Exception:
                pass

    def _handle_line(self, line: str):
        # started confirmation
        if self.re_started.search(line):
            discord.send_text(config["MESSAGES"]["start"])
            return

        mj = self._match_any(self.re_join, line)
        if mj:
            name, xuid = mj.group(1).strip(), mj.group(2).strip()
            if self._anti_spam_ok(xuid):
                ts = datetime.now()
                self.online[xuid] = PlayerSession(name, xuid, ts)
                try: mysqlm.on_join(name, xuid, ts)
                except Exception: pass
                discord.send_text(config["MESSAGES"]["join"].replace("{player}", name))
            return

        ml = self._match_any(self.re_leave, line)
        if ml:
            name, xuid = ml.group(1).strip(), ml.group(2).strip()
            if self._anti_spam_ok(xuid):
                ts = datetime.now()
                sess = self.online.pop(xuid, None)
                secs = int((ts - sess.joined_at).total_seconds()) if sess else 0
                try: mysqlm.on_leave(xuid, ts, secs)
                except Exception: pass
                discord.send_text(config["MESSAGES"]["leave"].replace("{player}", name))
            return

        # errors
        if config[self._sec("PARSER")].getboolean("detect_errors", fallback=True) and self.re_error.search(line):
            self.metrics.last_error_line = line[:220]
            discord.send_text(config["MESSAGES"]["error"].replace("{line}", self.metrics.last_error_line))

serverm = ServerManager(config)

# ---------------------- WATCHDOG ----------------------
class Watchdog:
    def __init__(self, cfg: configparser.ConfigParser, server: ServerManager):
        self.cfg = cfg
        self.server = server
        self.thread = None
        self.restart_history: List[datetime] = []
        self.awaiting_start_confirm = False

    def enabled(self) -> bool:
        return self.cfg[sec(self.server.profile,"WATCHDOG")].getboolean("enabled", fallback=True)

    def _sec(self) -> str:
        return sec(self.server.profile,"WATCHDOG")

    def _max_restarts_per_hour(self) -> int:
        try: return int(self.cfg[self._sec()].get("max_restarts_per_hour","5"))
        except ValueError: return 5

    def _hang_minutes(self) -> int:
        try: return int(self.cfg[self._sec()].get("hang_minutes","12"))
        except ValueError: return 12

    def _startup_timeout(self) -> int:
        try: return int(self.cfg[self._sec()].get("startup_timeout_seconds","120"))
        except ValueError: return 120

    def _backoff_base(self) -> int:
        try: return int(self.cfg[self._sec()].get("backoff_seconds","20"))
        except ValueError: return 20

    def _require_started_line(self) -> bool:
        return self.cfg[self._sec()].getboolean("require_started_line", fallback=True)

    def _lag_warn_only(self) -> bool:
        return self.cfg[self._sec()].getboolean("lag_warn_only", fallback=True)

    def _prune_history(self):
        cutoff = datetime.now() - timedelta(hours=1)
        self.restart_history = [t for t in self.restart_history if t >= cutoff]

    def _can_restart(self) -> bool:
        self._prune_history()
        return len(self.restart_history) < self._max_restarts_per_hour()

    def _schedule_backoff(self) -> int:
        self._prune_history()
        n = len(self.restart_history)
        delay = self._backoff_base() * (2 ** min(n, 4))
        return min(delay, 600)

    def loop(self):
        while True:
            time.sleep(5)

            if not self.enabled():
                continue

            # If process dead -> restart
            if self.server.proc and (self.server.proc.poll() is not None):
                if self._can_restart():
                    delay = self._schedule_backoff()
                    discord.send_text(config["MESSAGES"]["watchdog_restart"].replace("{reason}", f"crash, backoff {delay}s"))
                    time.sleep(delay)
                    self.restart_history.append(datetime.now())
                    try:
                        self.server.restart(reason="watchdog_crash")
                        self.awaiting_start_confirm = self._require_started_line()
                    except Exception:
                        pass
                else:
                    discord.send_text(config["MESSAGES"]["watchdog_giveup"])
                continue

            # Hang detection: no console activity for N minutes while running
            if self.server.is_running():
                last = self.server.metrics.last_console_activity
                if last and (datetime.now() - last) > timedelta(minutes=self._hang_minutes()):
                    self.server.metrics.last_hang_at = datetime.now()
                    reason = f"hang/lag ({self._hang_minutes()}m sin logs)"
                    if self._lag_warn_only():
                        discord.send_text("🧠 Posible lag detectado: " + reason)
                    else:
                        if self._can_restart():
                            delay = self._schedule_backoff()
                            discord.send_text(config["MESSAGES"]["watchdog_restart"].replace("{reason}", f"{reason}, backoff {delay}s"))
                            time.sleep(delay)
                            self.restart_history.append(datetime.now())
                            try:
                                self.server.restart(reason="watchdog_hang")
                                self.awaiting_start_confirm = self._require_started_line()
                            except Exception:
                                pass
                        else:
                            discord.send_text(config["MESSAGES"]["watchdog_giveup"])

            # Startup timeout
            if self._require_started_line() and self.server.is_running() and self.server.metrics.start_time:
                if self.awaiting_start_confirm:
                    if (datetime.now() - self.server.metrics.start_time).total_seconds() > self._startup_timeout():
                        if self._can_restart():
                            delay = self._schedule_backoff()
                            discord.send_text(config["MESSAGES"]["watchdog_restart"].replace("{reason}", f"startup timeout, backoff {delay}s"))
                            time.sleep(delay)
                            self.restart_history.append(datetime.now())
                            try:
                                self.server.restart(reason="watchdog_startup_timeout")
                                self.awaiting_start_confirm = True
                            except Exception:
                                pass
                        else:
                            discord.send_text(config["MESSAGES"]["watchdog_giveup"])

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

watchdog = Watchdog(config, serverm)

def watchdog_console_tap(line: str):
    if serverm.re_started.search(line):
        watchdog.awaiting_start_confirm = False

serverm.console_callbacks.append(watchdog_console_tap)

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

            if now.hour == hour and now.minute == minute:
                if self._last_run_date != now.date():
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
    def __init__(self, cfg: configparser.ConfigParser, server: ServerManager):
        self.cfg = cfg
        self.server = server
        self.thread = None

    def loop(self):
        while True:
            prof = self.server.profile
            enabled = self.cfg[sec(prof,"BACKUP")].getboolean("enabled", fallback=True)
            if enabled:
                try:
                    out = backupm.make_backup(prof)
                    fname = os.path.basename(out)
                    self.server.metrics.backups_ok += 1
                    self.server.metrics.last_backup_file = fname
                    discord.send_text(config["MESSAGES"]["backup_done"].replace("{file}", fname))
                except Exception as e:
                    self.server.metrics.backups_fail += 1
                    discord.send_text(config["MESSAGES"]["backup_fail"].replace("{reason}", str(e)[:120]))
            try:
                mins = int(self.cfg[sec(prof,"BACKUP")].get("interval_minutes", "60"))
            except ValueError:
                mins = 60
            time.sleep(max(1, mins) * 60)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

backups = BackupScheduler(config, serverm)

# ---------------------- AUTO-FIX SCHEDULER ----------------------
class AutoFixScheduler:
    def __init__(self, cfg: configparser.ConfigParser, server: ServerManager):
        self.cfg = cfg
        self.server = server
        self.thread = None

    def enabled(self) -> bool:
        return self.cfg[sec(self.server.profile,"AUTOFIX")].getboolean("enabled", fallback=True)

    def loop(self):
        while True:
            prof = self.server.profile
            enabled = self.cfg[sec(prof,"AUTOFIX")].getboolean("enabled", fallback=True)
            if enabled:
                try:
                    grace = int(self.cfg[sec(prof,"AUTOFIX")].get("grace_minutes", "10"))
                    fixed = mysqlm.close_hung_sessions(grace_minutes=grace, fix_reason="auto_fix_crash")
                    if fixed > 0 and self.cfg[sec(prof,"AUTOFIX")].getboolean("notify_discord", fallback=True):
                        discord.send_text(f"🧼 Auto-fix: cerradas {fixed} sesiones colgadas (>{grace}m).")
                except Exception:
                    pass
            try:
                mins = int(self.cfg[sec(prof,"AUTOFIX")].get("interval_minutes", "5"))
            except ValueError:
                mins = 5
            time.sleep(max(1, mins) * 60)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

autofix = AutoFixScheduler(config, serverm)

# ---------------------- MOTD SCHEDULER ----------------------
class MotdScheduler:
    def __init__(self, cfg: configparser.ConfigParser, server: ServerManager):
        self.cfg = cfg
        self.server = server
        self.thread = None
        self.idx = 0

    def loop(self):
        while True:
            prof = self.server.profile
            s = sec(prof, "MOTD")
            if self.cfg[s].getboolean("enabled", fallback=False):
                only_online = self.cfg[s].getboolean("only_if_players_online", fallback=True)
                if (not only_online) or (len(self.server.online) > 0):
                    raw = self.cfg[s].get("messages", "").strip()
                    msgs = [m.strip() for m in raw.split("|") if m.strip()]
                    if msgs:
                        msg = msgs[self.idx % len(msgs)]
                        self.idx += 1
                        if self.cfg[s].getboolean("use_prefix", fallback=True):
                            msg = config["MESSAGES"].get("motd_prefix", "📢 ") + msg
                        # in-game announce
                        try:
                            self.server.say_ingame(msg)
                        except Exception:
                            pass
            try:
                mins = int(self.cfg[sec(prof,"MOTD")].get("interval_minutes", "15"))
            except ValueError:
                mins = 15
            time.sleep(max(1, mins) * 60)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

motd = MotdScheduler(config, serverm)

# ---------------------- MAINTENANCE HELPERS ----------------------
def maintenance_on(profile: str):
    s = sec(profile, "MAINTENANCE")
    if not config[s].getboolean("enabled", fallback=True):
        return
    if config[s].getboolean("use_whitelist", fallback=True):
        serverm.send_command("whitelist on")
    discord.send_text(config["MESSAGES"]["maintenance_on"])

def maintenance_off(profile: str):
    s = sec(profile, "MAINTENANCE")
    if not config[s].getboolean("enabled", fallback=True):
        return
    if config[s].getboolean("use_whitelist", fallback=True):
        serverm.send_command("whitelist off")
    discord.send_text(config["MESSAGES"]["maintenance_off"])

def maintenance_kick_all(profile: str):
    s = sec(profile, "MAINTENANCE")
    if not config[s].getboolean("enabled", fallback=True):
        return
    if config[s].getboolean("kick_before_restart", fallback=True):
        msg = config[s].get("kick_message", "Reinicio programado.")
        # Bedrock supports kick <player> maybe; kick @a may not always. We'll use "say" warning and stop.
        serverm.say_ingame(msg)

# ---------------------- RESTART SCHEDULER ----------------------
class RestartScheduler:
    def __init__(self, cfg: configparser.ConfigParser, server: ServerManager):
        self.cfg = cfg
        self.server = server
        self.thread = None
        self._warned_at: Optional[datetime] = None
        self._next_restart: Optional[datetime] = None

    def compute_next(self) -> Optional[datetime]:
        prof = self.server.profile
        s = sec(prof, "RESTART")
        if not self.cfg[s].getboolean("enabled", fallback=False):
            return None
        mode = self.cfg[s].get("mode", "interval").strip().lower()

        now = datetime.now()
        if mode == "daily":
            t = self.cfg[s].get("daily_time", "04:00").strip()
            m = re.match(r"^(\d{1,2}):(\d{2})$", t)
            if not m:
                hour, minute = 4, 0
            else:
                hour, minute = int(m.group(1)), int(m.group(2))
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate = candidate + timedelta(days=1)
            return candidate

        # interval
        try:
            hours = float(self.cfg[s].get("interval_hours", "6"))
        except ValueError:
            hours = 6.0
        if self._next_restart is None:
            return now + timedelta(hours=hours)
        # otherwise keep existing if in future
        if self._next_restart > now:
            return self._next_restart
        return now + timedelta(hours=hours)

    def warn_and_restart_if_due(self):
        prof = self.server.profile
        s = sec(prof, "RESTART")
        if not self.cfg[s].getboolean("enabled", fallback=False):
            self._next_restart = None
            self._warned_at = None
            return
        if not self.server.is_running():
            # don't schedule if not running; keep recomputing
            self._warned_at = None
            self._next_restart = self.compute_next()
            return

        self._next_restart = self.compute_next()
        if self._next_restart is None:
            return

        try:
            warn_mins = int(self.cfg[s].get("warn_minutes", "5"))
        except ValueError:
            warn_mins = 5

        now = datetime.now()
        warn_time = self._next_restart - timedelta(minutes=warn_mins)

        if now >= warn_time and (self._warned_at is None or self._warned_at.date() != now.date() or (now - self._warned_at) > timedelta(minutes=1)):
            # send warn once per cycle
            self._warned_at = now
            maintenance_on(prof)
            # in-game warning
            self.server.say_ingame(config["MESSAGES"]["restart_warn_5m"])
            if self.cfg[s].getboolean("announce_discord", fallback=True):
                discord.send_text("⏳ Reinicio programado: " + config["MESSAGES"]["restart_warn_5m"])

        if now >= self._next_restart:
            # perform restart
            maintenance_kick_all(prof)
            self.server.say_ingame(config["MESSAGES"]["restart_now"])
            if self.cfg[s].getboolean("announce_discord", fallback=True):
                discord.send_text("♻️ Reinicio programado: apagando/encendiendo servidor...")
            self.server.restart(reason="scheduled_restart")
            # after restart, maintenance off
            time.sleep(6)
            maintenance_off(prof)
            # compute next interval
            self._next_restart = None
            self._warned_at = None

    def loop(self):
        while True:
            try:
                self.warn_and_restart_if_due()
            except Exception:
                pass
            time.sleep(5)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

restarts = RestartScheduler(config, serverm)

# ---------------------- GUI HELPERS ----------------------
def simple_prompt(root, title, label):
    win = tk.Toplevel(root)
    win.title(title)
    win.resizable(False, False)
    ttk.Label(win, text=label).pack(padx=10, pady=8)
    var = tk.StringVar()
    entry = ttk.Entry(win, textvariable=var, width=30)
    entry.pack(padx=10, pady=6)
    entry.focus_set()

    out = {"val": None}
    def ok():
        out["val"] = var.get()
        win.destroy()
    def cancel():
        win.destroy()

    btns = ttk.Frame(win)
    btns.pack(padx=10, pady=10, fill="x")
    ttk.Button(btns, text="OK", command=ok).pack(side="left")
    ttk.Button(btns, text="Cancelar", command=cancel).pack(side="right")

    win.grab_set()
    root.wait_window(win)
    return out["val"]

# ---------------------- GUI ----------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LatinBat Bedrock Manager v4")
        self.geometry("1120x780")
        self.minsize(980, 680)

        self.status_var = tk.StringVar(value="STOPPED")
        self.online_var = tk.StringVar(value="Online: 0")
        self.db_var = tk.StringVar(value="DB: ?")
        self.uptime_var = tk.StringVar(value="Uptime: 0m")
        self.metrics_var = tk.StringVar(value="Restarts: 0 | Backups OK: 0 | FAIL: 0")

        self.profile_var = tk.StringVar(value=active_profile())

        self._build_ui()
        self._wire_callbacks()
        self._load_webhook_into_entry()

        self.refresh_players_table()
        self._set_db_state()

        # start schedulers
        dailys.start()
        backups.start()
        watchdog.start()
        autofix.start()
        motd.start()
        restarts.start()

        # periodic UI refresh
        self.after(1000, self._tick_ui)

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)

        ttk.Label(top, text="Perfil:").pack(side="left")
        self.profile_box = ttk.Combobox(top, textvariable=self.profile_var, values=get_profiles(), state="readonly", width=18)
        self.profile_box.pack(side="left", padx=6)
        ttk.Button(top, text="Cambiar", command=self.on_change_profile).pack(side="left", padx=6)
        ttk.Button(top, text="➕ Nuevo perfil", command=self.on_add_profile).pack(side="left", padx=6)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Label(top, text="Estado:").pack(side="left")
        ttk.Label(top, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).pack(side="left", padx=(6, 18))
        ttk.Label(top, textvariable=self.online_var).pack(side="left")
        ttk.Label(top, textvariable=self.db_var).pack(side="left", padx=(18, 0))
        ttk.Label(top, textvariable=self.uptime_var).pack(side="left", padx=(18, 0))

        btns = ttk.Frame(top)
        btns.pack(side="right")
        ttk.Button(btns, text="▶ Iniciar", command=self.on_start).pack(side="left", padx=4)
        ttk.Button(btns, text="🛑 /stop", command=self.on_stop_soft).pack(side="left", padx=4)
        ttk.Button(btns, text="⛔ Stop", command=self.on_stop_hard).pack(side="left", padx=4)
        ttk.Button(btns, text="🔁 Reiniciar", command=self.on_restart).pack(side="left", padx=4)

        mline = ttk.Frame(self)
        mline.pack(fill="x", padx=10, pady=(0,6))
        ttk.Label(mline, textvariable=self.metrics_var).pack(side="left")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=8)

        self.tab_console = ttk.Frame(self.nb)
        self.tab_stats = ttk.Frame(self.nb)
        self.tab_discord = ttk.Frame(self.nb)
        self.tab_backup = ttk.Frame(self.nb)
        self.tab_settings = ttk.Frame(self.nb)
        self.tab_automation = ttk.Frame(self.nb)

        self.nb.add(self.tab_console, text="🖥️ Consola")
        self.nb.add(self.tab_stats, text="📊 Estadísticas")
        self.nb.add(self.tab_discord, text="🔔 Discord")
        self.nb.add(self.tab_backup, text="📦 Backups")
        self.nb.add(self.tab_automation, text="🧠 Automatización")
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
        ttk.Button(st_top, text="📊 Resumen diario ahora", command=self.on_run_daily_now).pack(side="left", padx=8)
        ttk.Button(st_top, text="⬇ Exportar CSV", command=self.on_export_csv).pack(side="left", padx=8)

        self.tree = ttk.Treeview(self.tab_stats, columns=("name","xuid","hours","last_seen"), show="headings")
        for col, title, w in [
            ("name","Jugador",240),
            ("xuid","XUID",270),
            ("hours","Horas",90),
            ("last_seen","Última vez",190)
        ]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0,8))

        # discord tab
        d_wrap = ttk.Frame(self.tab_discord)
        d_wrap.pack(fill="both", expand=True, padx=8, pady=8)

        wh = ttk.LabelFrame(d_wrap, text="🔐 Webhook (guardado protegido) — config.ini en UTF-8 (emojis OK)")
        wh.pack(fill="x", padx=6, pady=6)

        ttk.Label(wh, text="Webhook URL:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.webhook_entry = ttk.Entry(wh)
        self.webhook_entry.grid(row=0, column=1, sticky="we", padx=6, pady=6)
        wh.columnconfigure(1, weight=1)
        ttk.Button(wh, text="Guardar", command=self.on_save_webhook).grid(row=0, column=2, padx=6, pady=6)
        ttk.Button(wh, text="Probar", command=self.on_test_webhook).grid(row=0, column=3, padx=6, pady=6)

        msgf = ttk.LabelFrame(d_wrap, text="🎨 Mensajes (placeholders: {player} {date} {file} {line} {reason})")
        msgf.pack(fill="both", expand=True, padx=6, pady=6)

        self.msg_vars = {}
        msg_keys = ["join","leave","start","stop_soft","stop_hard","backup_done","backup_fail","error","watchdog_restart","watchdog_giveup","daily_title","restart_warn_5m","restart_now","maintenance_on","maintenance_off","motd_prefix"]
        labels = {k:k for k in msg_keys}
        labels.update({
            "join":"Join", "leave":"Leave", "start":"Start", "stop_soft":"Stop (/stop)",
            "stop_hard":"Stop (hard)", "backup_done":"Backup OK", "backup_fail":"Backup FAIL",
            "error":"Error", "watchdog_restart":"Watchdog restart", "watchdog_giveup":"Watchdog giveup",
            "daily_title":"Daily title", "restart_warn_5m":"Reinicio aviso (5m)", "restart_now":"Reinicio (ahora)",
            "maintenance_on":"Mantenimiento ON", "maintenance_off":"Mantenimiento OFF", "motd_prefix":"MOTD Prefix"
        })
        for r, k in enumerate(msg_keys):
            self.msg_vars[k] = tk.StringVar(value=config["MESSAGES"].get(k, DEFAULTS_GLOBAL["MESSAGES"].get(k, "")))
            ttk.Label(msgf, text=labels.get(k,k)).grid(row=r, column=0, sticky="w", padx=6, pady=4)
            e = ttk.Entry(msgf, textvariable=self.msg_vars[k])
            e.grid(row=r, column=1, sticky="we", padx=6, pady=4)
        msgf.columnconfigure(1, weight=1)

        btn_line = ttk.Frame(msgf)
        btn_line.grid(row=len(msg_keys), column=0, columnspan=2, sticky="we", padx=6, pady=6)
        ttk.Button(btn_line, text="💾 Guardar", command=self.on_save_messages).pack(side="left")
        ttk.Button(btn_line, text="👁️ Preview", command=self.on_preview_messages).pack(side="left", padx=6)

        emb = ttk.LabelFrame(d_wrap, text="🧩 Discord/Embeds")
        emb.pack(fill="x", padx=6, pady=6)

        self.discord_enabled = tk.BooleanVar(value=config["DISCORD"].getboolean("enabled", fallback=True))
        self.discord_mode = tk.StringVar(value=config["DISCORD"].get("mode","embed"))
        self.discord_username = tk.StringVar(value=config["DISCORD"].get("username","LatinBat Bot"))
        self.discord_avatar = tk.StringVar(value=config["DISCORD"].get("avatar_url",""))
        self.embed_enabled = tk.BooleanVar(value=config["EMBED"].getboolean("enabled", fallback=True))
        self.embed_color = tk.StringVar(value=config["EMBED"].get("color","3447003"))
        self.embed_footer = tk.StringVar(value=config["EMBED"].get("footer","LatinBat Bedrock Server"))

        ttk.Checkbutton(emb, text="Habilitar Discord", variable=self.discord_enabled).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(emb, text="Embeds habilitados", variable=self.embed_enabled).grid(row=0, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(emb, text="Modo").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(emb, textvariable=self.discord_mode, values=["embed","text"], state="readonly", width=10).grid(row=1, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(emb, text="Color").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(emb, textvariable=self.embed_color, width=14).grid(row=2, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(emb, text="Footer").grid(row=3, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(emb, textvariable=self.embed_footer, width=40).grid(row=3, column=1, sticky="we", padx=6, pady=4)

        ttk.Label(emb, text="Username").grid(row=4, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(emb, textvariable=self.discord_username, width=30).grid(row=4, column=1, sticky="we", padx=6, pady=4)

        ttk.Label(emb, text="Avatar URL").grid(row=5, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(emb, textvariable=self.discord_avatar, width=50).grid(row=5, column=1, sticky="we", padx=6, pady=4)

        ttk.Button(emb, text="💾 Guardar", command=self.on_save_discord_embed).grid(row=6, column=1, sticky="e", padx=6, pady=6)
        emb.columnconfigure(1, weight=1)

        # backup tab
        b = ttk.Frame(self.tab_backup)
        b.pack(fill="both", expand=True, padx=8, pady=8)
        bf = ttk.LabelFrame(b, text="Backups del perfil actual")
        bf.pack(fill="x", padx=6, pady=6)

        prof = self.profile_var.get()
        self.backup_enabled = tk.BooleanVar(value=config[sec(prof,"BACKUP")].getboolean("enabled", fallback=True))
        self.backup_interval = tk.StringVar(value=config[sec(prof,"BACKUP")].get("interval_minutes","60"))
        self.backup_path = tk.StringVar(value=config[sec(prof,"BACKUP")].get("path","backups"))
        self.backup_keep = tk.StringVar(value=config[sec(prof,"BACKUP")].get("keep_last","20"))
        self.backup_excl = tk.StringVar(value=config[sec(prof,"BACKUP")].get("exclude_patterns",".tmp;.lock"))

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

        ttk.Button(bf, text="💾 Guardar", command=self.on_save_backup).grid(row=5, column=1, sticky="e", padx=6, pady=6)
        ttk.Button(bf, text="📦 Backup ahora", command=self.on_backup_now).grid(row=5, column=2, sticky="e", padx=6, pady=6)
        bf.columnconfigure(1, weight=1)

        # automation tab
        a = ttk.Frame(self.tab_automation)
        a.pack(fill="both", expand=True, padx=8, pady=8)

        # Auto-fix
        af = ttk.LabelFrame(a, text="🧼 Auto-fix sesiones colgadas (MySQL)")
        af.pack(fill="x", padx=6, pady=6)
        prof = self.profile_var.get()
        self.autofix_enabled = tk.BooleanVar(value=config[sec(prof,"AUTOFIX")].getboolean("enabled", fallback=True))
        self.autofix_grace = tk.StringVar(value=config[sec(prof,"AUTOFIX")].get("grace_minutes","10"))
        self.autofix_interval = tk.StringVar(value=config[sec(prof,"AUTOFIX")].get("interval_minutes","5"))
        self.autofix_notify = tk.BooleanVar(value=config[sec(prof,"AUTOFIX")].getboolean("notify_discord", fallback=True))
        ttk.Checkbutton(af, text="Habilitar auto-fix", variable=self.autofix_enabled).grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Label(af, text="Grace (min)").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(af, textvariable=self.autofix_grace, width=8).grid(row=1, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(af, text="Intervalo (min)").grid(row=1, column=2, sticky="w", padx=6, pady=4)
        ttk.Entry(af, textvariable=self.autofix_interval, width=8).grid(row=1, column=3, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(af, text="Avisar Discord", variable=self.autofix_notify).grid(row=1, column=4, sticky="w", padx=6, pady=4)
        ttk.Button(af, text="💾 Guardar", command=self.on_save_autofix).grid(row=2, column=4, sticky="e", padx=6, pady=6)

        # MOTD
        mf = ttk.LabelFrame(a, text="🧾 MOTD / anuncios rotativos")
        mf.pack(fill="both", expand=True, padx=6, pady=6)
        self.motd_enabled = tk.BooleanVar(value=config[sec(prof,"MOTD")].getboolean("enabled", fallback=False))
        self.motd_interval = tk.StringVar(value=config[sec(prof,"MOTD")].get("interval_minutes","15"))
        self.motd_only_online = tk.BooleanVar(value=config[sec(prof,"MOTD")].getboolean("only_if_players_online", fallback=True))
        self.motd_use_prefix = tk.BooleanVar(value=config[sec(prof,"MOTD")].getboolean("use_prefix", fallback=True))
        raw_msgs = config[sec(prof,"MOTD")].get("messages","")
        self.motd_text = tk.Text(mf, height=6, wrap="word")
        ttk.Checkbutton(mf, text="Habilitar MOTD", variable=self.motd_enabled).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Label(mf, text="Intervalo (min)").grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Entry(mf, textvariable=self.motd_interval, width=8).grid(row=0, column=2, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(mf, text="Solo si hay jugadores", variable=self.motd_only_online).grid(row=0, column=3, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(mf, text="Usar prefix", variable=self.motd_use_prefix).grid(row=0, column=4, sticky="w", padx=6, pady=4)

        ttk.Label(mf, text="Mensajes (1 por línea):").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.motd_text.grid(row=2, column=0, columnspan=5, sticky="we", padx=6, pady=4)
        self.motd_text.insert("1.0", "\n".join([m.strip() for m in raw_msgs.split("|") if m.strip()]))
        ttk.Button(mf, text="💾 Guardar MOTD", command=self.on_save_motd).grid(row=3, column=4, sticky="e", padx=6, pady=6)
        ttk.Button(mf, text="📢 Enviar ahora", command=self.on_motd_send_now).grid(row=3, column=3, sticky="e", padx=6, pady=6)
        mf.columnconfigure(4, weight=1)

        # Scheduled restart + maintenance
        rf = ttk.LabelFrame(a, text="🔁 Reinicio programado + 🔒 Modo mantenimiento")
        rf.pack(fill="x", padx=6, pady=6)
        self.restart_enabled = tk.BooleanVar(value=config[sec(prof,"RESTART")].getboolean("enabled", fallback=False))
        self.restart_mode = tk.StringVar(value=config[sec(prof,"RESTART")].get("mode","interval"))
        self.restart_interval_h = tk.StringVar(value=config[sec(prof,"RESTART")].get("interval_hours","6"))
        self.restart_daily = tk.StringVar(value=config[sec(prof,"RESTART")].get("daily_time","04:00"))
        self.restart_warn_m = tk.StringVar(value=config[sec(prof,"RESTART")].get("warn_minutes","5"))
        self.restart_discord = tk.BooleanVar(value=config[sec(prof,"RESTART")].getboolean("announce_discord", fallback=True))

        self.maint_enabled = tk.BooleanVar(value=config[sec(prof,"MAINTENANCE")].getboolean("enabled", fallback=True))
        self.maint_whitelist = tk.BooleanVar(value=config[sec(prof,"MAINTENANCE")].getboolean("use_whitelist", fallback=True))
        self.maint_kick = tk.BooleanVar(value=config[sec(prof,"MAINTENANCE")].getboolean("kick_before_restart", fallback=True))
        self.maint_kick_msg = tk.StringVar(value=config[sec(prof,"MAINTENANCE")].get("kick_message","Reinicio programado."))

        ttk.Checkbutton(rf, text="Habilitar reinicio programado", variable=self.restart_enabled).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Label(rf, text="Modo").grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Combobox(rf, textvariable=self.restart_mode, values=["interval","daily"], state="readonly", width=10).grid(row=0, column=2, sticky="w", padx=6, pady=4)
        ttk.Label(rf, text="Cada (horas)").grid(row=0, column=3, sticky="w", padx=6, pady=4)
        ttk.Entry(rf, textvariable=self.restart_interval_h, width=8).grid(row=0, column=4, sticky="w", padx=6, pady=4)
        ttk.Label(rf, text="Hora diaria").grid(row=1, column=1, sticky="w", padx=6, pady=4)
        ttk.Entry(rf, textvariable=self.restart_daily, width=10).grid(row=1, column=2, sticky="w", padx=6, pady=4)
        ttk.Label(rf, text="Aviso (min)").grid(row=1, column=3, sticky="w", padx=6, pady=4)
        ttk.Entry(rf, textvariable=self.restart_warn_m, width=8).grid(row=1, column=4, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(rf, text="Avisar Discord", variable=self.restart_discord).grid(row=1, column=0, sticky="w", padx=6, pady=4)

        ttk.Separator(rf).grid(row=2, column=0, columnspan=5, sticky="we", pady=6)

        ttk.Checkbutton(rf, text="Habilitar mantenimiento", variable=self.maint_enabled).grid(row=3, column=0, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(rf, text="Usar whitelist", variable=self.maint_whitelist).grid(row=3, column=1, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(rf, text="Aviso in-game (kick msg)", variable=self.maint_kick).grid(row=3, column=2, sticky="w", padx=6, pady=4)
        ttk.Label(rf, text="Mensaje mantenimiento").grid(row=4, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(rf, textvariable=self.maint_kick_msg, width=60).grid(row=4, column=1, columnspan=4, sticky="we", padx=6, pady=4)

        ttk.Button(rf, text="💾 Guardar reinicio/mantenimiento", command=self.on_save_restart_maint).grid(row=5, column=4, sticky="e", padx=6, pady=6)
        rf.columnconfigure(4, weight=1)

        # settings tab
        s = ttk.Frame(self.tab_settings)
        s.pack(fill="both", expand=True, padx=8, pady=8)

        sf = ttk.LabelFrame(s, text="Servidor (perfil actual)")
        sf.pack(fill="x", padx=6, pady=6)

        self.server_path_var = tk.StringVar(value=config[sec(self.profile_var.get(),"SERVER")].get("exe_path","bedrock_server.exe"))
        ttk.Label(sf, text="bedrock_server.exe").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(sf, textvariable=self.server_path_var, width=55).grid(row=0, column=1, sticky="we", padx=6, pady=6)
        ttk.Button(sf, text="Buscar", command=self.on_pick_server_exe).grid(row=0, column=2, padx=6, pady=6)
        sf.columnconfigure(1, weight=1)

        wf = ttk.LabelFrame(s, text="Watchdog / Lag detector (perfil actual)")
        wf.pack(fill="x", padx=6, pady=6)

        prof = self.profile_var.get()
        self.wd_enabled = tk.BooleanVar(value=config[sec(prof,"WATCHDOG")].getboolean("enabled", fallback=True))
        self.wd_hang = tk.StringVar(value=config[sec(prof,"WATCHDOG")].get("hang_minutes","12"))
        self.wd_max = tk.StringVar(value=config[sec(prof,"WATCHDOG")].get("max_restarts_per_hour","5"))
        self.wd_backoff = tk.StringVar(value=config[sec(prof,"WATCHDOG")].get("backoff_seconds","20"))
        self.wd_startup = tk.StringVar(value=config[sec(prof,"WATCHDOG")].get("startup_timeout_seconds","120"))
        self.wd_require = tk.BooleanVar(value=config[sec(prof,"WATCHDOG")].getboolean("require_started_line", fallback=True))
        self.wd_warn_only = tk.BooleanVar(value=config[sec(prof,"WATCHDOG")].getboolean("lag_warn_only", fallback=True))

        ttk.Checkbutton(wf, text="Habilitar watchdog", variable=self.wd_enabled).grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Label(wf, text="Hang/Lag (min)").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(wf, textvariable=self.wd_hang, width=8).grid(row=1, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(wf, text="Max reinicios/h").grid(row=1, column=2, sticky="w", padx=6, pady=4)
        ttk.Entry(wf, textvariable=self.wd_max, width=8).grid(row=1, column=3, sticky="w", padx=6, pady=4)
        ttk.Label(wf, text="Backoff (s)").grid(row=1, column=4, sticky="w", padx=6, pady=4)
        ttk.Entry(wf, textvariable=self.wd_backoff, width=8).grid(row=1, column=5, sticky="w", padx=6, pady=4)

        ttk.Label(wf, text="Startup timeout (s)").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(wf, textvariable=self.wd_startup, width=10).grid(row=2, column=1, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(wf, text="Requiere 'Server started.'", variable=self.wd_require).grid(row=2, column=2, columnspan=2, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(wf, text="Lag: solo avisar (no reiniciar)", variable=self.wd_warn_only).grid(row=2, column=4, columnspan=2, sticky="w", padx=6, pady=4)

        ttk.Button(wf, text="💾 Guardar watchdog", command=self.on_save_watchdog).grid(row=3, column=5, sticky="e", padx=6, pady=6)

        df = ttk.LabelFrame(s, text="Scheduler diario (global)")
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
        ttk.Button(df, text="💾 Guardar diario", command=self.on_save_daily).grid(row=2, column=5, sticky="e", padx=6, pady=6)

    def _wire_callbacks(self):
        serverm.console_callbacks.append(self.on_console_line)
        serverm.status_callbacks.append(self.on_status_change)

    def _load_webhook_into_entry(self):
        url = discord.webhook_url()
        self.webhook_entry.delete(0, "end")
        self.webhook_entry.insert(0, url)

    def _tick_ui(self):
        self.online_var.set(f"Online: {len(serverm.online)}")
        self.uptime_var.set(f"Uptime: {serverm.metrics.uptime_str()}")
        self.metrics_var.set(
            f"Restarts: {serverm.metrics.restarts_total} | "
            f"Backups OK: {serverm.metrics.backups_ok} | FAIL: {serverm.metrics.backups_fail} | "
            f"Último backup: {serverm.metrics.last_backup_file or '-'}"
        )
        self.after(1000, self._tick_ui)

    def log_to_console(self, text: str):
        self.console.configure(state="normal")
        self.console.insert("end", text + "\n")
        self.console.see("end")
        self.console.configure(state="disabled")

    def on_console_line(self, line: str):
        self.after(0, lambda: self.log_to_console(line))

    def on_status_change(self, status: str):
        self.after(0, lambda: self.status_var.set(status))
        self.after(0, self._set_db_state)

    def _set_db_state(self):
        if mysqlm.enabled():
            try:
                mysqlm.connect()
                self.db_var.set("DB: OK")
            except Exception:
                self.db_var.set("DB: ERROR")
        else:
            self.db_var.set("DB: OFF")

    # ---- profile ----
    def on_change_profile(self):
        prof = self.profile_var.get().strip()
        if not prof:
            return
        set_active_profile(prof)
        serverm.set_profile(prof)
        self._reload_profile_bound_vars()
        messagebox.showinfo("OK", f"Perfil activo: {prof}")

    def on_add_profile(self):
        name = simple_prompt(self, "Nuevo perfil", "Nombre del perfil (sin espacios):")
        if not name:
            return
        name = name.strip()
        if not re.match(r"^[A-Za-z0-9_\-]+$", name):
            messagebox.showerror("Error", "Nombre inválido. Usa letras/números/_-")
            return
        profiles = get_profiles()
        if name in profiles:
            messagebox.showerror("Error", "Ese perfil ya existe.")
            return
        profiles.append(name)
        config["GLOBAL"]["profiles"] = ",".join(profiles)
        for secname, kv in DEFAULTS_PROFILE.items():
            config[sec(name, secname)] = dict(kv)
        save_config()
        self.profile_box["values"] = profiles
        self.profile_var.set(name)
        self.on_change_profile()

    def _reload_profile_bound_vars(self):
        prof = self.profile_var.get()
        # server
        self.server_path_var.set(config[sec(prof,"SERVER")].get("exe_path","bedrock_server.exe"))
        # backups
        self.backup_enabled.set(config[sec(prof,"BACKUP")].getboolean("enabled", fallback=True))
        self.backup_interval.set(config[sec(prof,"BACKUP")].get("interval_minutes","60"))
        self.backup_path.set(config[sec(prof,"BACKUP")].get("path","backups"))
        self.backup_keep.set(config[sec(prof,"BACKUP")].get("keep_last","20"))
        self.backup_excl.set(config[sec(prof,"BACKUP")].get("exclude_patterns",".tmp;.lock"))
        # watchdog
        self.wd_enabled.set(config[sec(prof,"WATCHDOG")].getboolean("enabled", fallback=True))
        self.wd_hang.set(config[sec(prof,"WATCHDOG")].get("hang_minutes","12"))
        self.wd_max.set(config[sec(prof,"WATCHDOG")].get("max_restarts_per_hour","5"))
        self.wd_backoff.set(config[sec(prof,"WATCHDOG")].get("backoff_seconds","20"))
        self.wd_startup.set(config[sec(prof,"WATCHDOG")].get("startup_timeout_seconds","120"))
        self.wd_require.set(config[sec(prof,"WATCHDOG")].getboolean("require_started_line", fallback=True))
        self.wd_warn_only.set(config[sec(prof,"WATCHDOG")].getboolean("lag_warn_only", fallback=True))
        # autofix
        self.autofix_enabled.set(config[sec(prof,"AUTOFIX")].getboolean("enabled", fallback=True))
        self.autofix_grace.set(config[sec(prof,"AUTOFIX")].get("grace_minutes","10"))
        self.autofix_interval.set(config[sec(prof,"AUTOFIX")].get("interval_minutes","5"))
        self.autofix_notify.set(config[sec(prof,"AUTOFIX")].getboolean("notify_discord", fallback=True))
        # motd text
        self.motd_enabled.set(config[sec(prof,"MOTD")].getboolean("enabled", fallback=False))
        self.motd_interval.set(config[sec(prof,"MOTD")].get("interval_minutes","15"))
        self.motd_only_online.set(config[sec(prof,"MOTD")].getboolean("only_if_players_online", fallback=True))
        self.motd_use_prefix.set(config[sec(prof,"MOTD")].getboolean("use_prefix", fallback=True))
        raw_msgs = config[sec(prof,"MOTD")].get("messages","")
        self.motd_text.delete("1.0","end")
        self.motd_text.insert("1.0", "\n".join([m.strip() for m in raw_msgs.split("|") if m.strip()]))
        # restart/maint
        self.restart_enabled.set(config[sec(prof,"RESTART")].getboolean("enabled", fallback=False))
        self.restart_mode.set(config[sec(prof,"RESTART")].get("mode","interval"))
        self.restart_interval_h.set(config[sec(prof,"RESTART")].get("interval_hours","6"))
        self.restart_daily.set(config[sec(prof,"RESTART")].get("daily_time","04:00"))
        self.restart_warn_m.set(config[sec(prof,"RESTART")].get("warn_minutes","5"))
        self.restart_discord.set(config[sec(prof,"RESTART")].getboolean("announce_discord", fallback=True))
        self.maint_enabled.set(config[sec(prof,"MAINTENANCE")].getboolean("enabled", fallback=True))
        self.maint_whitelist.set(config[sec(prof,"MAINTENANCE")].getboolean("use_whitelist", fallback=True))
        self.maint_kick.set(config[sec(prof,"MAINTENANCE")].getboolean("kick_before_restart", fallback=True))
        self.maint_kick_msg.set(config[sec(prof,"MAINTENANCE")].get("kick_message","Reinicio programado."))

        self.refresh_players_table()

    # ---- server controls ----
    def on_start(self):
        try:
            prof = self.profile_var.get()
            req = config[sec(prof,"WATCHDOG")].getboolean("require_started_line", fallback=True)
            watchdog.awaiting_start_confirm = req
            serverm.start()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def on_stop_soft(self):
        serverm.stop_soft()
        discord.send_text(config["MESSAGES"]["stop_soft"])
        try:
            mysqlm.insert_event(serverm.profile, "stop", "soft_stop", serverm.metrics.uptime_seconds())
        except Exception:
            pass

    def on_stop_hard(self):
        serverm.stop_hard()
        discord.send_text(config["MESSAGES"]["stop_hard"])
        try:
            mysqlm.insert_event(serverm.profile, "stop", "hard_stop", serverm.metrics.uptime_seconds())
        except Exception:
            pass

    def on_restart(self):
        try:
            discord.send_text(config["MESSAGES"]["watchdog_restart"].replace("{reason}", "manual"))
            serverm.restart(reason="manual")
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

    def on_run_daily_now(self):
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

    def on_export_csv(self):
        try:
            rows = mysqlm.fetch_players()
        except Exception:
            messagebox.showerror("Error", "No se pudo leer MySQL.")
            return
        if not rows:
            messagebox.showinfo("Info", "No hay datos.")
            return
        out = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")], initialdir=BASE_DIR)
        if not out:
            return
        try:
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["name","xuid","total_hours","last_seen"])
                for name, xuid, total_seconds, last_seen in rows:
                    w.writerow([name, xuid, round(int(total_seconds)/3600,2), str(last_seen)])
            messagebox.showinfo("OK", f"CSV exportado:\n{out}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ---- discord ----
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
        sample = {
            "{player}":"Steve",
            "{date}":str(date.today()),
            "{file}":"backup_test.zip",
            "{line}":"ERROR demo",
            "{reason}":"hang"
        }
        preview = []
        for k, var in self.msg_vars.items():
            t = var.get()
            for a, b in sample.items():
                t = t.replace(a, b)
            preview.append(f"{k}: {t}")
        messagebox.showinfo("Preview", "\n\n".join(preview))

    def on_save_discord_embed(self):
        config["DISCORD"]["enabled"] = "true" if self.discord_enabled.get() else "false"
        config["DISCORD"]["mode"] = self.discord_mode.get()
        config["DISCORD"]["username"] = self.discord_username.get().strip()
        config["DISCORD"]["avatar_url"] = self.discord_avatar.get().strip()

        config["EMBED"]["enabled"] = "true" if self.embed_enabled.get() else "false"
        config["EMBED"]["color"] = self.embed_color.get().strip() or "3447003"
        config["EMBED"]["footer"] = self.embed_footer.get().strip() or "LatinBattle.com - Bedrock Manager"

        save_config()
        messagebox.showinfo("OK", "Discord/Embeds guardados.")

    # ---- backups ----
    def on_pick_backup_dir(self):
        d = filedialog.askdirectory(initialdir=BASE_DIR)
        if d:
            try:
                rel = os.path.relpath(d, BASE_DIR)
                if not rel.startswith(".."):
                    self.backup_path.set(rel)
                else:
                    self.backup_path.set(d)
            except Exception:
                self.backup_path.set(d)

    def on_save_backup(self):
        prof = self.profile_var.get()
        s = sec(prof,"BACKUP")
        config[s]["enabled"] = "true" if self.backup_enabled.get() else "false"
        config[s]["interval_minutes"] = self.backup_interval.get().strip() or "60"
        config[s]["path"] = self.backup_path.get().strip() or "backups"
        config[s]["keep_last"] = self.backup_keep.get().strip() or "20"
        config[s]["exclude_patterns"] = self.backup_excl.get().strip()
        save_config()
        messagebox.showinfo("OK", "Backups guardados.")

    def on_backup_now(self):
        prof = self.profile_var.get()
        try:
            out = backupm.make_backup(prof)
            fname = os.path.basename(out)
            serverm.metrics.backups_ok += 1
            serverm.metrics.last_backup_file = fname
            discord.send_text(config["MESSAGES"]["backup_done"].replace("{file}", fname))
            messagebox.showinfo("OK", f"Backup creado:\n{out}")
        except Exception as e:
            serverm.metrics.backups_fail += 1
            discord.send_text(config["MESSAGES"]["backup_fail"].replace("{reason}", str(e)[:120]))
            messagebox.showerror("Error", str(e))

    # ---- automation ----
    def on_save_autofix(self):
        prof = self.profile_var.get()
        s = sec(prof,"AUTOFIX")
        config[s]["enabled"] = "true" if self.autofix_enabled.get() else "false"
        config[s]["grace_minutes"] = self.autofix_grace.get().strip() or "10"
        config[s]["interval_minutes"] = self.autofix_interval.get().strip() or "5"
        config[s]["notify_discord"] = "true" if self.autofix_notify.get() else "false"
        save_config()
        messagebox.showinfo("OK", "Auto-fix guardado.")

    def on_save_motd(self):
        prof = self.profile_var.get()
        s = sec(prof,"MOTD")
        config[s]["enabled"] = "true" if self.motd_enabled.get() else "false"
        config[s]["interval_minutes"] = self.motd_interval.get().strip() or "15"
        config[s]["only_if_players_online"] = "true" if self.motd_only_online.get() else "false"
        config[s]["use_prefix"] = "true" if self.motd_use_prefix.get() else "false"
        lines = [l.strip() for l in self.motd_text.get("1.0","end").splitlines() if l.strip()]
        config[s]["messages"] = "|".join(lines)
        save_config()
        messagebox.showinfo("OK", "MOTD guardado.")

    def on_motd_send_now(self):
        prof = self.profile_var.get()
        lines = [l.strip() for l in self.motd_text.get("1.0","end").splitlines() if l.strip()]
        if not lines:
            messagebox.showinfo("Info", "No hay mensajes.")
            return
        msg = lines[0]
        if self.motd_use_prefix.get():
            msg = config["MESSAGES"].get("motd_prefix", "📢 ") + msg
        serverm.say_ingame(msg)

    def on_save_restart_maint(self):
        prof = self.profile_var.get()
        # restart
        rs = sec(prof,"RESTART")
        config[rs]["enabled"] = "true" if self.restart_enabled.get() else "false"
        config[rs]["mode"] = self.restart_mode.get()
        config[rs]["interval_hours"] = self.restart_interval_h.get().strip() or "6"
        config[rs]["daily_time"] = self.restart_daily.get().strip() or "04:00"
        config[rs]["warn_minutes"] = self.restart_warn_m.get().strip() or "5"
        config[rs]["announce_discord"] = "true" if self.restart_discord.get() else "false"
        # maintenance
        ms = sec(prof,"MAINTENANCE")
        config[ms]["enabled"] = "true" if self.maint_enabled.get() else "false"
        config[ms]["use_whitelist"] = "true" if self.maint_whitelist.get() else "false"
        config[ms]["kick_before_restart"] = "true" if self.maint_kick.get() else "false"
        config[ms]["kick_message"] = self.maint_kick_msg.get().strip()
        save_config()
        messagebox.showinfo("OK", "Reinicio/Mantenimiento guardados.")

    # ---- settings ----
    def on_pick_server_exe(self):
        p = filedialog.askopenfilename(
            initialdir=BASE_DIR,
            title="Selecciona bedrock_server.exe",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")]
        )
        if p:
            try:
                rel = os.path.relpath(p, BASE_DIR)
                if not rel.startswith(".."):
                    self.server_path_var.set(rel)
                else:
                    self.server_path_var.set(p)
            except Exception:
                self.server_path_var.set(p)

            prof = self.profile_var.get()
            config[sec(prof,"SERVER")]["exe_path"] = self.server_path_var.get()
            save_config()

    def on_save_watchdog(self):
        prof = self.profile_var.get()
        s = sec(prof,"WATCHDOG")
        config[s]["enabled"] = "true" if self.wd_enabled.get() else "false"
        config[s]["hang_minutes"] = self.wd_hang.get().strip() or "12"
        config[s]["max_restarts_per_hour"] = self.wd_max.get().strip() or "5"
        config[s]["backoff_seconds"] = self.wd_backoff.get().strip() or "20"
        config[s]["startup_timeout_seconds"] = self.wd_startup.get().strip() or "120"
        config[s]["require_started_line"] = "true" if self.wd_require.get() else "false"
        config[s]["lag_warn_only"] = "true" if self.wd_warn_only.get() else "false"
        save_config()
        messagebox.showinfo("OK", "Watchdog guardado.")

    def on_save_daily(self):
        config["DAILY"]["enabled"] = "true" if self.daily_enabled.get() else "false"
        config["DAILY"]["hour"] = self.daily_hour.get().strip() or "0"
        config["DAILY"]["minute"] = self.daily_min.get().strip() or "5"
        config["DAILY"]["top_n"] = self.daily_top.get().strip() or "5"
        save_config()
        messagebox.showinfo("OK", "Scheduler diario guardado.")

def main():
    try:
        mysqlm.ensure_tables()
    except Exception:
        pass
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()