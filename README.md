# 🧱🔥 LatinBat Bedrock Manager (Minecraft Bedrock Dedicated Server)

Un **manager todo-en-uno** para servidores **Minecraft Bedrock Dedicated Server (BDS)** con:
- 🖥️ GUI en **Tkinter**
- 🔔 Notificaciones a **Discord Webhook**
- 📊 Estadísticas en **MySQL**
- 📦 Backups automáticos + rotación
- 🛡️ Watchdog (auto-restart + detección de hang)
- 📄 Página **PHP** para ver estadísticas por jugador

> 🌐 Proyecto para **LatinBattle.com**  
> 🤖 Creado por **ChatGPT (OpenAI)** y **Azzlaer** para **LatinBattle.com**

---

## ✨ Características

### 🖥️ Manager Python (GUI)
- ▶️ Iniciar servidor Bedrock
- 🛑 Apagar con `/stop` (apagado limpio)
- ⛔ Stop brusco (terminate / taskkill opcional en Windows)
- 🔁 Reiniciar
- 🧾 Consola en vivo (log streaming)

### 🔔 Discord
- ✍️ Editor visual de mensajes (con emojis) desde la GUI  
- 🧩 Embeds configurables (modo embed o texto)
- 🔐 Webhook guardado **protegido** (tokenización/obfuscación en `config.ini`)
- 🧯 Alertas pro del watchdog (crash/hang/startup timeout)

### 📊 MySQL Stats
- 👤 Registro de jugadores (XUID, first_seen, last_seen)
- 🕒 Sesiones (join/leave/segundos por sesión)
- 📅 Estadísticas diarias automáticas (jugadores únicos y tiempo total)
- ⬇️ Exportar CSV desde GUI

### 📦 Backups
- 🧰 Backups ZIP automáticos cada *N* minutos
- ♻️ Rotación (mantener los últimos *N*)
- 🗂️ Carpeta configurable por perfil
- 🚫 Exclusiones por patrón (`.tmp;.lock;...`)
- 🧪 Backup manual desde GUI

### 🛡️ Watchdog
- ♻️ Auto-restart si el proceso cae
- 🧠 Detección de “hang” si no hay actividad de consola por X minutos
- ⏳ Backoff incremental para reinicios
- 🧯 Límite de reinicios por hora (evita loops)
- ✅ Puede exigir confirmación `Server started.` (startup timeout)

### 🌐 Web PHP (sin framework)
- 🔎 Buscar por nombre o XUID
- 👤 Perfil completo del jugador
- 🕒 Sesiones paginadas
- 📅 Stats diarias (últimos 30 días) + top 10 por día

---

## 🧩 Requisitos

### ✅ Servidor
- Windows 10/11 x64 (recomendado), o Linux (con ajustes de paths)
- **Minecraft Bedrock Dedicated Server** (BDS)
- Python **3.10+** (recomendado 3.12)
- MySQL/MariaDB

### ✅ Python dependencies
- `requests`
- `mysql-connector-python`
- `pyinstaller` (solo para generar EXE)

---

## 📥 Instalación rápida

### 1) Descargar Bedrock Dedicated Server
Descargá BDS desde el sitio oficial:
- https://www.minecraft.net/en-us/download/server/bedrock

Extraé el servidor (donde está `bedrock_server.exe`) y poné el manager en la **misma carpeta**.

---

### 2) Crear base de datos MySQL
Ejecutá este SQL:

```sql
CREATE DATABASE IF NOT EXISTS latinbat_bedrock
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

USE latinbat_bedrock;

CREATE TABLE IF NOT EXISTS players (
  xuid VARCHAR(32) PRIMARY KEY,
  name VARCHAR(32) NOT NULL,
  first_seen DATETIME NOT NULL,
  last_seen DATETIME NOT NULL,
  total_seconds BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  xuid VARCHAR(32) NOT NULL,
  join_time DATETIME NOT NULL,
  leave_time DATETIME NULL,
  session_seconds INT NOT NULL DEFAULT 0,
  INDEX (xuid),
  INDEX (join_time),
  FOREIGN KEY (xuid) REFERENCES players(xuid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS daily_stats (
  stat_date DATE PRIMARY KEY,
  unique_players INT NOT NULL DEFAULT 0,
  total_seconds BIGINT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sessions_xuid_join_time ON sessions (xuid, join_time);
```

> 💡 Importante: la BD usa `utf8mb4` para soportar emojis.

---

### 3) Instalar dependencias
En la carpeta del proyecto:

```bat
python -m pip install --upgrade pip
python -m pip install requests mysql-connector-python pyinstaller
```

---

## ▶️ Uso

### Ejecutar el manager
```bat
python bedrock_manager.py
```

La primera vez se creará `config.ini` automáticamente (UTF-8).

---

## ⚙️ Configuración (`config.ini`)

### Perfiles múltiples
El manager soporta múltiples servidores mediante perfiles:

- `GLOBAL.active_profile` define el perfil activo
- `GLOBAL.profiles` lista perfiles separados por coma

Cada perfil crea secciones como:
- `PROFILE:default:SERVER`
- `PROFILE:default:BACKUP`
- `PROFILE:default:WATCHDOG`
- `PROFILE:default:PARSER`

Ejemplo de perfil:
```ini
[GLOBAL]
active_profile=default
profiles=default,us_server

[PROFILE:default:SERVER]
exe_path=bedrock_server.exe
workdir=
encoding=utf-8
hard_kill_tree_windows=false
```

---

### 🔔 Discord Webhook (seguro)
En la GUI:
- Pestaña **Discord** → pegás el webhook → **Guardar**
- El webhook se guarda **protegido** (no se ve en texto plano)

---

### 🧩 Mensajes y Embeds
En la GUI:
- Pestaña **Discord** → editor de mensajes

Placeholders disponibles:
- `{player}`
- `{date}`
- `{file}`
- `{line}`
- `{reason}`

---

### 📦 Backups
En la GUI:
- Pestaña **Backups**
- Configurás:
  - intervalo
  - carpeta
  - rotación
  - exclusiones

---

### 🛡️ Watchdog
En la GUI:
- Pestaña **Ajustes** → Watchdog

Recomendado:
- `hang_minutes = 10-15`
- `max_restarts_per_hour = 3-5`
- `startup_timeout_seconds = 120-180`

---

## 🧪 Generar ejecutable (EXE)

```bat
python -m PyInstaller --onefile --windowed --clean bedrock_manager.py
```

Salida:
- `dist/bedrock_manager.exe`

> 💡 Si te sale `'pyinstaller' is not recognized` usá siempre `python -m PyInstaller ...`

---

## 🌐 Página PHP (estadísticas)

Archivo: **`user.php`**

✅ Funciones:
- Buscar por nombre o XUID
- Perfil del jugador
- Sesiones paginadas
- Stats diarias + top del día

Configurar DB en el encabezado del archivo:
```php
$dbHost = "localhost";
$dbName = "latinbat_bedrock";
$dbUser = "bedrock_srv";
$dbPass = "";
```

Abrir:
- `http://localhost/user.php`
- `user.php?xuid=TU_XUID`

---

## 🧠 Limitaciones conocidas

- ⚠️ Eventos como **muertes**, **día/noche**, **clima**, etc. **no siempre salen** en el log oficial del BDS (sin addons).  
  Este proyecto se basa en los eventos que el server imprime en consola.

---

## 🗺️ Roadmap (ideas)
- 🧩 Multi-instancia real (varios servidores corriendo en paralelo desde una sola GUI)
- 📈 Dashboard completo (ranking semanal/mensual + gráficos)
- 🔑 Seguridad fuerte del webhook con DPAPI/Keyring (según SO)
- 🧪 Tests automáticos del parser

---

## 🤝 Créditos
- 🤖 **ChatGPT (OpenAI)**  
- 🧙‍♂️ **Azzlaer**  
- 🌐 Para **LatinBattle.com**

---

## 📜 Licencia
Elegí la que quieras (MIT recomendada).  
Si querés, te genero también un `LICENSE` MIT automáticamente.
