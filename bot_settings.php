<?php
require_once __DIR__ . '/core/app.php';
ensure_installed();
verify_csrf();
$user = require_login();

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

$rentalId = (int)($_POST['rental_id'] ?? 0);
$returnAnchor = trim((string)($_POST['return_anchor'] ?? ''));
$rental   = find_rental($rentalId);

if (!$rental || (int)$rental['user_id'] !== (int)$user['id']) {
    if ($wantsJson) json_out(false, 'Аренда не найдена.');
    flash('error', 'Аренда не найдена.');
    redirect('dashboard.php');
}

$plan = find_plan((int)($rental['plan_id'] ?? 0));
$hasGames = $plan && plan_has_feature($plan, 'games');

$delayMin    = max(0, min(60,  (float)($_POST['delay_min']    ?? 0.5)));
$delayMax    = max(0, min(120, (float)($_POST['delay_max']    ?? 1.5)));
$userCooldown = max(0, min(3600, (int)($_POST['user_cooldown'] ?? 0)));
$rpm         = max(0, min(600,  (int)($_POST['rpm']           ?? 0)));
$mentionOnly = isset($_POST['mention_only']) ? 1 : 0;
$gameMode    = ($hasGames && isset($_POST['game_mode'])) ? 1 : 0;
$validTypes  = ['guess', 'hangman', 'quiz', 'words', 'truefalse'];
$gameType    = ($hasGames && in_array($_POST['game_type'] ?? '', $validTypes)) ? $_POST['game_type'] : 'guess';

if ($delayMin > $delayMax) $delayMax = $delayMin;

update_rental_settings($rentalId, (int)$user['id'], [
    'delay_min'     => $delayMin,
    'delay_max'     => $delayMax,
    'user_cooldown' => $userCooldown,
    'rpm'           => $rpm,
    'mention_only'  => $mentionOnly,
    'game_mode'     => $gameMode,
    'game_type'     => $gameType,
]);

if ($wantsJson) {
    json_out(true, 'Настройки бота сохранены. Перезапустите бота, чтобы они применились.', [
        'settings' => [
            'delay_min' => $delayMin,
            'delay_max' => $delayMax,
            'user_cooldown' => $userCooldown,
            'rpm' => $rpm,
            'mention_only' => (int)$mentionOnly,
            'game_mode' => (int)$gameMode,
            'game_type' => (string)$gameType,
        ],
    ]);
}

flash('success', 'Настройки бота сохранены. Перезапустите бота, чтобы они применились.');
$anchor = '';
if ($returnAnchor !== '' && preg_match('/^#?[a-zA-Z0-9_-]+$/', $returnAnchor)) {
    $anchor = $returnAnchor[0] === '#' ? $returnAnchor : ('#' . $returnAnchor);
}
redirect('dashboard.php' . $anchor);
