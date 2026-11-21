<?php
// 地域レポートの無料ダウンロード（/report/area/）で使う共通の部品。エックスサーバーの PHP で動く。
// 申し込みの記録と PDF は、公開フォルダの外（jimofuku.jp/leads/ と jimofuku.jp/reports/）に置く。

mb_language('uni');   // UTF-8 で送る
mb_internal_encoding('UTF-8');
date_default_timezone_set('Asia/Tokyo');

const SITE_NAME = 'ジモふく';
const SITE_URL = 'https://jimofuku.jp';
const FROM = 'noreply@jimofuku.jp';
const TOKEN_DAYS = 30;     // メールのリンクが使える日数

$BASE = dirname(__DIR__, 3);      // jimofuku.jp/（public_html の1つ上）
$LEADS = "$BASE/leads";
$PDFS = "$BASE/reports";

function h(string $s): string { return htmlspecialchars($s, ENT_QUOTES, 'UTF-8'); }

function page_out(int $code, string $title, string $html): void {
    http_response_code($code);
    header('Content-Type: text/html; charset=utf-8');
    $t = h($title);
    echo <<<HTML
<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{$t}｜ジモふく</title>
<link rel="stylesheet" href="../../assets/site.css"></head>
<body><main><div class="container"><section class="form-done">
<h1>{$t}</h1>{$html}
<p><a href="./">地域レポートのページへもどる</a></p>
</section></div></main></body></html>
HTML;
    exit;
}

function fail(int $code, string $msg): void {
    page_out($code, 'うまくいきませんでした', '<p>' . h($msg) . '</p><p><button type="button" class="btn" onclick="history.back()">入力画面にもどる</button></p>');
}

function field(string $k, int $max): string {
    $v = isset($_POST[$k]) && is_string($_POST[$k]) ? $_POST[$k] : '';
    $v = str_replace(["\r\n", "\r", "\n"], ' ', $v);
    $v = preg_replace('/[\x00-\x1F\x7F]/u', '', $v) ?? '';
    $v = trim($v);
    if (mb_strlen($v) > $max) fail(400, "入力が長すぎます（{$max}文字まで）。");
    return $v;
}

// 作成済みのレポートの一覧（build.py が書き出す）
function catalog(): array {
    $c = json_decode((string)@file_get_contents(__DIR__ . '/files.json'), true);
    return is_array($c) ? $c : ['cities' => [], 'editions' => [], 'roles' => []];
}

// 運営者のアドレス（お問い合わせと同じ、公開フォルダの外の contact_to.txt）
function owner_to(): string {
    global $BASE;
    $to = @file_get_contents("$BASE/contact_to.txt");
    $to = $to === false ? '' : trim($to);
    return filter_var($to, FILTER_VALIDATE_EMAIL) ? $to : '';
}

// 送りすぎを防ぐ。IP アドレスは元に戻せない形で、3日だけ残す
function rate_limit(string $name, int $per_ip, int $window, int $per_day): void {
    global $LEADS;
    $dir = "$LEADS/log";
    if (!is_dir($dir)) @mkdir($dir, 0700, true);
    $now = time();
    $ipkey = hash('sha256', ($_SERVER['REMOTE_ADDR'] ?? '') . '|' . __DIR__);
    $fp = @fopen("$dir/$name-" . date('Ymd') . '.log', 'c+');
    if (!$fp) return;
    flock($fp, LOCK_EX);
    $lines = array_filter(explode("\n", stream_get_contents($fp)));
    if (count($lines) >= $per_day) fail(429, '本日は申し込みが多いため、受付を止めています。明日もう一度お試しください。');
    $mine = 0;
    foreach ($lines as $l) {
        [$t, $k] = array_pad(explode(' ', $l, 2), 2, '');
        if ($k === $ipkey && $now - (int)$t < $window) $mine++;
    }
    if ($mine >= $per_ip) fail(429, '続けて送られています。10分ほどたってから、もう一度お試しください。');
    fseek($fp, 0, SEEK_END);
    fwrite($fp, "$now $ipkey\n");
    flock($fp, LOCK_UN);
    fclose($fp);
    foreach (glob("$dir/*.log") ?: [] as $old) {
        if (filemtime($old) < $now - 3 * 86400) @unlink($old);
    }
}

function same_site(): void {
    $origin = $_SERVER['HTTP_ORIGIN'] ?? '';
    $host = preg_replace('/:\d+$/', '', $_SERVER['HTTP_HOST'] ?? '');
    if ($origin !== '' && parse_url($origin, PHP_URL_HOST) !== $host) fail(403, 'このページからは送れません。');
}

function send_mail(string $to, string $subject, string $text, string $reply_to = '', array $attach = []): bool {
    $from = 'From: ' . mb_encode_mimeheader(SITE_NAME) . ' <' . FROM . ">\r\n";
    if ($reply_to !== '') $from .= "Reply-To: $reply_to\r\n";
    if (!$attach) return mb_send_mail($to, $subject, $text, rtrim($from), '-f' . FROM);
    // 添付つき（運営者へのリストの送付だけで使う）
    $b = 'jf' . bin2hex(random_bytes(8));
    $headers = $from . "MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"$b\"";
    $body = "--$b\r\nContent-Type: text/plain; charset=UTF-8\r\nContent-Transfer-Encoding: base64\r\n\r\n"
        . chunk_split(base64_encode($text)) . "\r\n";
    foreach ($attach as $name => $data) {
        $n = preg_match('/^[\w.\-]+$/', $name) ? $name : '=?UTF-8?B?' . base64_encode($name) . '?=';
        $body .= "--$b\r\nContent-Type: text/csv; charset=UTF-8; name=\"$n\"\r\nContent-Transfer-Encoding: base64\r\n"
            . "Content-Disposition: attachment; filename=\"$n\"\r\n\r\n" . chunk_split(base64_encode($data)) . "\r\n";
    }
    $body .= "--$b--\r\n";
    return mail($to, '=?UTF-8?B?' . base64_encode($subject) . '?=', $body, $headers, '-f' . FROM);
}

function mail_footer(): string {
    return "\n──────────\n" . SITE_NAME . "（障害福祉の事業所をさがせるサイト）\n" . SITE_URL . "/\n運営：システムM\n"
        . "このメールは送信専用のアドレスから送っています。ご質問は " . SITE_URL . "/contact/ からお寄せください。\n";
}
