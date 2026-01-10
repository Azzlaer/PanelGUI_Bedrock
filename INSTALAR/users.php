<?php
// user.php - LatinBat Bedrock Stats (single page)
// PHP 8+ recomendado

declare(strict_types=1);

// -------------------- CONFIG DB --------------------
$dbHost = "localhost";
$dbName = "latinbat_bedrock";
$dbUser = "bedrock_srv";
$dbPass = "";
$dbPort = 3306;

// -------------------- PDO --------------------
$dsn = "mysql:host={$dbHost};port={$dbPort};dbname={$dbName};charset=utf8mb4";
$options = [
  PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
  PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
  PDO::ATTR_EMULATE_PREPARES => false,
];

try {
  $pdo = new PDO($dsn, $dbUser, $dbPass, $options);
} catch (Throwable $e) {
  http_response_code(500);
  echo "<h1>Error DB</h1><pre>" . htmlspecialchars($e->getMessage()) . "</pre>";
  exit;
}

// -------------------- HELPERS --------------------
function h(string $s): string { return htmlspecialchars($s, ENT_QUOTES, "UTF-8"); }

function seconds_to_hm(int $sec): string {
  $h = intdiv($sec, 3600);
  $m = intdiv($sec % 3600, 60);
  return "{$h}h {$m}m";
}

// -------------------- INPUTS --------------------
$q = trim((string)($_GET["q"] ?? ""));         // búsqueda
$xuid = trim((string)($_GET["xuid"] ?? ""));  // perfil directo
$page = max(1, (int)($_GET["page"] ?? 1));
$perPage = 25;
$offset = ($page - 1) * $perPage;

$day = trim((string)($_GET["day"] ?? "")); // para ver top de un día (YYYY-MM-DD)
if ($day !== "" && !preg_match('/^\d{4}-\d{2}-\d{2}$/', $day)) $day = "";

// -------------------- SEARCH RESULTS --------------------
$searchResults = [];
if ($xuid === "" && $q !== "") {
  // buscar por nombre (like) o xuid exacto si coincide
  $stmt = $pdo->prepare("
    SELECT xuid, name, total_seconds, last_seen
    FROM players
    WHERE name LIKE :q OR xuid = :x
    ORDER BY last_seen DESC
    LIMIT 50
  ");
  $stmt->execute([
    ":q" => "%" . $q . "%",
    ":x" => $q
  ]);
  $searchResults = $stmt->fetchAll();

  // si hay un match exacto por XUID, autoselecciona
  foreach ($searchResults as $r) {
    if ($r["xuid"] === $q) { $xuid = $q; break; }
  }
}

// -------------------- PLAYER PROFILE --------------------
$player = null;
if ($xuid !== "") {
  $stmt = $pdo->prepare("
    SELECT xuid, name, first_seen, last_seen, total_seconds
    FROM players
    WHERE xuid = :x
    LIMIT 1
  ");
  $stmt->execute([":x" => $xuid]);
  $player = $stmt->fetch() ?: null;
}

// -------------------- PLAYER SESSIONS (paginated) --------------------
$sessions = [];
$totalSessions = 0;

if ($player) {
  $stmt = $pdo->prepare("SELECT COUNT(*) AS c FROM sessions WHERE xuid = :x");
  $stmt->execute([":x" => $xuid]);
  $totalSessions = (int)$stmt->fetch()["c"];

  $stmt = $pdo->prepare("
    SELECT id, join_time, leave_time, session_seconds
    FROM sessions
    WHERE xuid = :x
    ORDER BY join_time DESC
    LIMIT :lim OFFSET :off
  ");
  $stmt->bindValue(":x", $xuid, PDO::PARAM_STR);
  $stmt->bindValue(":lim", $perPage, PDO::PARAM_INT);
  $stmt->bindValue(":off", $offset, PDO::PARAM_INT);
  $stmt->execute();
  $sessions = $stmt->fetchAll();
}

// -------------------- DAILY STATS (last 30 days) --------------------
$dailyStats = [];
$stmt = $pdo->query("
  SELECT stat_date, unique_players, total_seconds
  FROM daily_stats
  ORDER BY stat_date DESC
  LIMIT 30
");
$dailyStats = $stmt->fetchAll();

// Top players for selected day (optional)
$topPlayers = [];
if ($day !== "") {
  $stmt = $pdo->prepare("
    SELECT p.name, SUM(s.session_seconds) AS seconds
    FROM sessions s
    JOIN players p ON p.xuid = s.xuid
    WHERE DATE(s.join_time) = :d
    GROUP BY p.name
    ORDER BY seconds DESC
    LIMIT 10
  ");
  $stmt->execute([":d" => $day]);
  $topPlayers = $stmt->fetchAll();
}

$totalPages = $player ? (int)ceil($totalSessions / $perPage) : 1;

// -------------------- HTML --------------------
?>
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LatinBat Bedrock - Stats</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; background:#0b1220; color:#e8eefc; margin:0; }
    .wrap { max-width: 1100px; margin: 0 auto; padding: 18px; }
    .card { background:#121a2b; border:1px solid #24304a; border-radius:14px; padding:14px; margin:12px 0; }
    h1,h2,h3 { margin: 8px 0; }
    a { color:#7db2ff; text-decoration:none; }
    a:hover { text-decoration:underline; }
    .row { display:flex; gap:12px; flex-wrap:wrap; }
    .col { flex:1; min-width: 280px; }
    input[type=text] { width:100%; padding:10px; border-radius:10px; border:1px solid #2b3858; background:#0e1627; color:#e8eefc; }
    button { padding:10px 14px; border-radius:10px; border:1px solid #2b3858; background:#1a2640; color:#e8eefc; cursor:pointer; }
    button:hover { background:#22335a; }
    table { width:100%; border-collapse: collapse; }
    th, td { text-align:left; padding:10px; border-bottom:1px solid #23304a; vertical-align: top; }
    th { color:#bcd1ff; font-weight:600; }
    .badge { display:inline-block; padding:3px 8px; border-radius:999px; border:1px solid #2b3858; background:#0e1627; color:#bcd1ff; font-size:12px; }
    .muted { color:#9bb0d6; }
    .pager { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .pill { padding:6px 10px; border-radius:999px; border:1px solid #2b3858; background:#0e1627; }
    .danger { color:#ffb4b4; }
  </style>
</head>
<body>
<div class="wrap">
  <h1>📊 LatinBat Bedrock - Estadísticas</h1>
  <div class="card">
    <form method="get">
      <div class="row">
        <div class="col">
          <label class="muted">Buscar jugador por nombre o XUID</label>
          <input type="text" name="q" value="<?=h($q)?>" placeholder="Ej: Steve o 2535412345678901">
        </div>
        <div style="min-width:160px; display:flex; align-items:flex-end;">
          <button type="submit">Buscar</button>
        </div>
      </div>
      <?php if ($xuid !== "" && $q !== ""): ?>
        <div class="muted" style="margin-top:8px;">
          Mostrando perfil para XUID: <span class="badge"><?=h($xuid)?></span>
        </div>
      <?php endif; ?>
    </form>
  </div>

  <?php if ($q !== "" && !$player): ?>
    <div class="card">
      <h2>Resultados</h2>
      <?php if (!$searchResults): ?>
        <p class="muted">Sin resultados.</p>
      <?php else: ?>
        <table>
          <thead>
            <tr>
              <th>Jugador</th>
              <th>XUID</th>
              <th>Horas</th>
              <th>Última vez</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <?php foreach ($searchResults as $r): ?>
              <tr>
                <td><?=h($r["name"])?></td>
                <td><span class="badge"><?=h($r["xuid"])?></span></td>
                <td><?=h(number_format(((int)$r["total_seconds"]/3600), 2))?></td>
                <td><?=h((string)$r["last_seen"])?></td>
                <td><a href="?xuid=<?=urlencode($r["xuid"])?>">Ver perfil →</a></td>
              </tr>
            <?php endforeach; ?>
          </tbody>
        </table>
      <?php endif; ?>
    </div>
  <?php endif; ?>

  <?php if ($xuid !== "" && !$player): ?>
    <div class="card">
      <h2 class="danger">Jugador no encontrado</h2>
      <p class="muted">No existe un jugador con XUID <?=h($xuid)?>.</p>
    </div>
  <?php endif; ?>

  <?php if ($player): ?>
    <div class="row">
      <div class="col card">
        <h2>👤 Perfil del jugador</h2>
        <p><strong>Nombre:</strong> <?=h($player["name"])?></p>
        <p><strong>XUID:</strong> <span class="badge"><?=h($player["xuid"])?></span></p>
        <p><strong>Primera vez:</strong> <?=h((string)$player["first_seen"])?></p>
        <p><strong>Última vez:</strong> <?=h((string)$player["last_seen"])?></p>
        <p><strong>Tiempo total:</strong> <?=h(seconds_to_hm((int)$player["total_seconds"]))?> <span class="muted">(<?=h(number_format(((int)$player["total_seconds"]/3600),2))?>h)</span></p>
      </div>

      <div class="col card">
        <h2>📈 Resumen rápido</h2>
        <p class="muted">Sesiones registradas: <span class="badge"><?=h((string)$totalSessions)?></span></p>
        <p class="muted">Promedio por sesión (aprox): 
          <?php
            $avg = $totalSessions > 0 ? (int)($player["total_seconds"] / $totalSessions) : 0;
            echo "<span class='badge'>" . h(seconds_to_hm($avg)) . "</span>";
          ?>
        </p>
        <p class="muted">Tips: compartí este link directo del jugador:</p>
        <p><span class="badge"><?=h("user.php?xuid=".$player["xuid"])?></span></p>
      </div>
    </div>

    <div class="card">
      <h2>🕒 Sesiones (últimas)</h2>
      <?php if (!$sessions): ?>
        <p class="muted">No hay sesiones registradas.</p>
      <?php else: ?>
        <table>
          <thead>
            <tr>
              <th>Join</th>
              <th>Leave</th>
              <th>Duración</th>
              <th>ID</th>
            </tr>
          </thead>
          <tbody>
            <?php foreach ($sessions as $s): ?>
              <tr>
                <td><?=h((string)$s["join_time"])?></td>
                <td><?=h((string)($s["leave_time"] ?? "-"))?></td>
                <td><?=h(seconds_to_hm((int)$s["session_seconds"]))?></td>
                <td class="muted">#<?=h((string)$s["id"])?></td>
              </tr>
            <?php endforeach; ?>
          </tbody>
        </table>

        <div class="pager" style="margin-top:12px;">
          <?php
            $base = "xuid=" . urlencode($xuid);
            if ($page > 1) {
              echo '<a class="pill" href="?' . $base . '&page=' . ($page-1) . '">← Anterior</a>';
            } else {
              echo '<span class="pill muted">← Anterior</span>';
            }
            echo '<span class="pill">Página ' . h((string)$page) . ' / ' . h((string)$totalPages) . '</span>';
            if ($page < $totalPages) {
              echo '<a class="pill" href="?' . $base . '&page=' . ($page+1) . '">Siguiente →</a>';
            } else {
              echo '<span class="pill muted">Siguiente →</span>';
            }
          ?>
        </div>
      <?php endif; ?>
    </div>
  <?php endif; ?>

  <div class="card">
    <h2>📅 Estadísticas diarias (últimos 30 días)</h2>
    <p class="muted">Haz click en un día para ver el Top 10.</p>
    <?php if (!$dailyStats): ?>
      <p class="muted">Sin datos diarios aún.</p>
    <?php else: ?>
      <table>
        <thead>
          <tr>
            <th>Día</th>
            <th>Jugadores únicos</th>
            <th>Tiempo total</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <?php foreach ($dailyStats as $d): ?>
            <tr>
              <td><span class="badge"><?=h((string)$d["stat_date"])?></span></td>
              <td><?=h((string)$d["unique_players"])?></td>
              <td><?=h(seconds_to_hm((int)$d["total_seconds"]))?></td>
              <td><a href="?<?= $player ? ("xuid=".urlencode($player["xuid"])."&") : "" ?>day=<?=urlencode($d["stat_date"])?>">Ver Top →</a></td>
            </tr>
          <?php endforeach; ?>
        </tbody>
      </table>
    <?php endif; ?>
  </div>

  <?php if ($day !== ""): ?>
    <div class="card">
      <h2>🏆 Top 10 del día <?=h($day)?></h2>
      <?php if (!$topPlayers): ?>
        <p class="muted">Sin sesiones ese día.</p>
      <?php else: ?>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Jugador</th>
              <th>Tiempo</th>
            </tr>
          </thead>
          <tbody>
            <?php foreach ($topPlayers as $i => $tp): ?>
              <tr>
                <td><?=h((string)($i+1))?></td>
                <td><?=h($tp["name"])?></td>
                <td><?=h(seconds_to_hm((int)$tp["seconds"]))?></td>
              </tr>
            <?php endforeach; ?>
          </tbody>
        </table>
      <?php endif; ?>
    </div>
  <?php endif; ?>

  <div class="card">
    <p class="muted">Hecho para LatinBat Bedrock. UTF-8 / utf8mb4 listo para emojis.</p>
  </div>

</div>
</body>
</html>
