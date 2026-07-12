<?php
require_once __DIR__ . '/core/app.php';
ensure_installed();
verify_csrf();
$user = require_login();

$rentalId = (int)($_POST['rental_id'] ?? 0);
$action   = trim($_POST['action'] ?? '');
$returnAnchor = trim((string)($_POST['return_anchor'] ?? ''));

$wantsJson = false;
if (!empty($_SERVER['HTTP_X_REQUESTED_WITH']) && strtolower((string)$_SERVER['HTTP_X_REQUESTED_WITH']) === 'xmlhttprequest') {
    $wantsJson = true;
} elseif (!empty($_SERVER['HTTP_ACCEPT']) && stripos((string)$_SERVER['HTTP_ACCEPT'], 'application/json') !== false) {
    $wantsJson = true;
}

function json_out(bool $ok, string $message = '', array $extra = []): void {
    header('Content-Type: application/json; charset=UTF-8');
    echo json_encode(array_merge(['ok' => $ok, 'message' => $message], $extra), JSON_UNESCAPED_UNICODE);
    exit;
}

$rental = find_rental($rentalId);

if (!$rental || (int)$rental['user_id'] !== (int)$user['id']) {
    if ($wantsJson) json_out(false, 'Аренда не найдена.');
    flash('error', 'Аренда не найдена.');
    redirect('dashboard.php');
}

if ($rental['status'] !== 'active') {
    if ($wantsJson) json_out(false, 'Аренда неактивна или истекла.');
    flash('error', 'Аренда неактивна или истекла.');
    redirect('dashboard.php');
}

$chatUrl     = $rental['chat_url'] ?? '';
$botNick     = $rental['bot_name'] ?? 'Bot';
$botType     = $rental['bot_type'] ?? 'guest';
$botEmail    = $rental['bot_email'] ?? '';
$botPass     = $rental['bot_password'] ?? '';
$botDir      = __DIR__ . '/storage/bots';
$pidFile     = $botDir . '/rental_' . $rentalId . '.pid';
$logFile     = $botDir . '/rental_' . $rentalId . '.log';
$botScript   = __DIR__ . '/bot.py';

$bs           = $rental['bot_settings'] ?? [];
$delayMin     = (float)($bs['delay_min']     ?? 0.5);
$delayMax     = (float)($bs['delay_max']     ?? 1.5);
$userCooldown = (int)($bs['user_cooldown']   ?? 0);
$rpm          = (int)($bs['rpm']             ?? 0);
$mentionOnly  = !empty($bs['mention_only']);
$gameMode     = !empty($bs['game_mode']);
$validGameTypes = ['guess', 'hangman', 'quiz', 'words', 'truefalse'];
$gameType     = in_array($bs['game_type'] ?? '', $validGameTypes) ? $bs['game_type'] : 'guess';

if (!is_dir($botDir)) mkdir($botDir, 0775, true);

/* ── helpers ── */

function bot_is_running(string $pidFile): bool {
    if (!file_exists($pidFile)) return false;
    $pid = (int)trim(file_get_contents($pidFile));
    if ($pid <= 0) return false;
    return posix_kill($pid, 0) !== false;
}

function kill_bot(string $pidFile): void {
    if (!file_exists($pidFile)) return;
    $pid = (int)trim(file_get_contents($pidFile));
    if ($pid > 0) {
        posix_kill($pid, 15);
        usleep(400000); // 0.4s
        if (posix_kill($pid, 0)) posix_kill($pid, 9);
    }
    @unlink($pidFile);
}

/* ── actions ── */

if ($action === 'start') {
    if (empty($chatUrl)) {
        file_put_contents($logFile, '[' . date('Y-m-d H:i:s') . "] ERROR: Адрес чата не указан.\n");
        if ($wantsJson) json_out(false, 'Адрес чата не указан.', ['bot_status' => 'error']);
        flash('error', 'Адрес чата не указан.');
        redirect('dashboard.php');
    }

    // Kill any already-running instance for this rental
    if (bot_is_running($pidFile)) kill_bot($pidFile);

    // Truncate old log
    file_put_contents($logFile, '');

    // Resolve absolute python path so PHP shell_exec always finds it
    $pythonBin = trim(shell_exec('which python3 2>/dev/null') ?: '');
    if ($pythonBin === '') $pythonBin = '/home/runner/workspace/.pythonlibs/bin/python3';

    // Determine correct weights file: shared or personal
    $effectiveWeights = effective_weights_path($rentalId, (int)$user['id'], $bs);

    // Plan-based feature flags
    $plan = find_plan((int)($rental['plan_id'] ?? 0));
    $isLiveChat = $plan && plan_has_feature($plan, 'live_chat');

    // Build the command
    $extraArgs = '';
    if (file_exists($effectiveWeights)) $extraArgs .= ' --weights ' . escapeshellarg($effectiveWeights);
    if ($delayMin !== 0.5)          $extraArgs .= ' --delay-min '     . escapeshellarg((string)$delayMin);
    if ($delayMax !== 1.5)          $extraArgs .= ' --delay-max '     . escapeshellarg((string)$delayMax);
    if ($userCooldown > 0)          $extraArgs .= ' --user-cooldown ' . (int)$userCooldown;
    if ($rpm > 0)                   $extraArgs .= ' --rpm '           . (int)$rpm;
    if ($mentionOnly)               $extraArgs .= ' --mention-only';
    if ($gameMode)                  $extraArgs .= ' --game-mode --game-type ' . escapeshellarg($gameType);
    if ($isLiveChat)                $extraArgs .= ' --live-chat-presence';
    $broadcastFile = __DIR__ . '/storage/broadcast.json';
    $extraArgs .= ' --broadcast-file ' . escapeshellarg($broadcastFile);

    if ($botType === 'registered' && $botEmail !== '' && $botPass !== '') {
        $cmd = sprintf(
            'nohup %s %s %s %s %s %s --email %s --password %s',
            escapeshellarg($pythonBin),
            escapeshellarg($botScript),
            escapeshellarg($chatUrl),
            escapeshellarg($botNick),
            escapeshellarg($pidFile),
            escapeshellarg($logFile),
            escapeshellarg($botEmail),
            escapeshellarg($botPass)
        ) . $extraArgs . ' > /dev/null 2>&1 &';
    } else {
        $cmd = sprintf(
            'nohup %s %s %s %s %s %s',
            escapeshellarg($pythonBin),
            escapeshellarg($botScript),
            escapeshellarg($chatUrl),
            escapeshellarg($botNick),
            escapeshellarg($pidFile),
            escapeshellarg($logFile)
        ) . $extraArgs . ' > /dev/null 2>&1 &';
    }

    shell_exec($cmd);

    $started = false;
    for ($i = 0; $i < 8; $i++) {
        usleep(500000);
        if (bot_is_running($pidFile)) { $started = true; break; }
    }

    if ($started) {
        // Give bot a moment to attempt login, then read early log
        usleep(1500000); // 1.5s more
        $logContent = file_exists($logFile) ? trim(file_get_contents($logFile)) : '';
        $lastLine = $logContent ? (explode("\n", $logContent)[count(explode("\n", $logContent))-1]) : '';

        // Check if log contains an error
        if (stripos($logContent, 'ОШИБКА') !== false || stripos($logContent, 'ERROR') !== false) {
            // Bot failed during login
            kill_bot($pidFile);
            $errLine = '';
            foreach (array_reverse(explode("\n", $logContent)) as $line) {
                if (stripos($line, 'ошибка') !== false || stripos($line, 'error') !== false) {
                    $errLine = preg_replace('/^\[[^\]]+\]\s*/', '', $line);
                    break;
                }
            }
            update_rental_bot_status($rentalId, (int)$user['id'], 'error', $errLine ?: 'Ошибка запуска бота');
            if ($wantsJson) json_out(false, 'Не удалось войти в чат: ' . ($errLine ?: 'смотрите лог'), ['bot_status' => 'error', 'note' => $errLine ?: 'Ошибка запуска бота']);
            flash('error', 'Не удалось войти в чат: ' . ($errLine ?: 'смотрите лог'));
        } else {
            update_rental_bot_status($rentalId, (int)$user['id'], 'running', 'Бот запущен и входит в чат…');
            if ($wantsJson) json_out(true, 'Бот «' . $botNick . '» запущен и входит в чат.', ['bot_status' => 'running', 'note' => 'Бот запущен и входит в чат…']);
            flash('success', 'Бот «' . $botNick . '» запущен и входит в чат.');
        }
    } else {
        $msg = 'Процесс бота не запустился';
        file_put_contents($logFile, '[' . date('Y-m-d H:i:s') . '] ERROR: ' . $msg . "\n", FILE_APPEND);
        update_rental_bot_status($rentalId, (int)$user['id'], 'error', $msg);
        if ($wantsJson) json_out(false, 'Не удалось запустить бота. Проверьте настройки сервера.', ['bot_status' => 'error', 'note' => $msg]);
        flash('error', 'Не удалось запустить бота. Проверьте настройки сервера.');
    }

} elseif ($action === 'stop') {
    if (bot_is_running($pidFile)) {
        kill_bot($pidFile);
    }
    update_rental_bot_status($rentalId, (int)$user['id'], 'stopped', 'Остановлен пользователем');
    if ($wantsJson) json_out(true, 'Бот «' . $botNick . '» остановлен и вышел из чата.', ['bot_status' => 'stopped', 'note' => 'Остановлен пользователем']);
    flash('success', 'Бот «' . $botNick . '» остановлен и вышел из чата.');

} elseif ($action === 'status') {
    // Sync running status from PID file
    $isRunning = bot_is_running($pidFile);
    $logContent = file_exists($logFile) ? trim(file_get_contents($logFile)) : '';
    $lines = array_filter(explode("\n", $logContent));
    $lastLine = $lines ? preg_replace('/^\[[^\]]+\]\s*/', '', end($lines)) : '';

    if (!$isRunning && ($rental['bot_status'] ?? '') === 'running') {
        // Bot died unexpectedly
        update_rental_bot_status($rentalId, (int)$user['id'], 'error', 'Бот неожиданно завершился: ' . $lastLine);
        if ($wantsJson) json_out(false, 'Бот неожиданно остановился.', ['bot_status' => 'error', 'note' => 'Бот неожиданно завершился: ' . $lastLine]);
        flash('error', 'Бот неожиданно остановился.');
    }
    if ($wantsJson) {
        $status = $isRunning ? 'running' : (($rental['bot_status'] ?? 'stopped') ?: 'stopped');
        json_out(true, 'OK', ['bot_status' => $status, 'note' => $lastLine]);
    }
} else {
    if ($wantsJson) json_out(false, 'Неизвестное действие.');
    flash('error', 'Неизвестное действие.');
}

$anchor = '';
if ($returnAnchor !== '' && preg_match('/^#?[a-zA-Z0-9_-]+$/', $returnAnchor)) {
    $anchor = $returnAnchor[0] === '#' ? $returnAnchor : ('#' . $returnAnchor);
}
redirect('dashboard.php' . $anchor);
