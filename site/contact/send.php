<?php
// お問い合わせフォーム（/contact/）の送り先。エックスサーバーの PHP で動く。
// 届け先のメールアドレスは公開フォルダの外（jimofuku.jp/contact_to.txt）に置く。
// GitHub Actions のアップロードで、Secrets の CONTACT_TO から書き込む。

mb_language('uni');   // UTF-8 で送る（ISO-2022-JP だと①や髙などが化ける）
mb_internal_encoding('UTF-8');
date_default_timezone_set('Asia/Tokyo');

const SITE_NAME = 'ジモふく';
const FROM = 'noreply@jimofuku.jp';
const KINDS = ['掲載内容のまちがい', 'サイトの使い方', 'レポートについて', '取材・お仕事のご相談', 'その他'];
const PER_IP_10MIN = 3;    // 同じ人から10分に送れる数
const PER_DAY = 50;        // サイト全体で1日に送れる数（いたずらでメールがあふれないように）

$base = dirname(__DIR__, 2);   // jimofuku.jp/（public_html の1つ上）

function fail(int $code, string $msg): void {
    http_response_code($code);
    header('Content-Type: text/html; charset=utf-8');
    $m = htmlspecialchars($msg, ENT_QUOTES, 'UTF-8');
    echo <<<HTML
<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>送れませんでした｜ジモふく</title>
<link rel="stylesheet" href="../assets/site.css"></head>
<body><main><div class="container"><section class="form-done">
<h1>送れませんでした</h1><p>{$m}</p>
<p><button type="button" class="btn" onclick="history.back()">入力画面にもどる</button></p>
</section></div></main></body></html>
HTML;
    exit;
}

function field(string $k, int $max): string {
    $v = isset($_POST[$k]) && is_string($_POST[$k]) ? $_POST[$k] : '';
    $v = str_replace(["\r\n", "\r"], "\n", $v);
    $v = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/u', '', $v) ?? '';
    $v = trim($v);
    if (mb_strlen($v) > $max) fail(400, "入力が長すぎます（{$max}文字まで）。");
    return $v;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    header('Location: ./', true, 303);
    exit;
}

// ほかのサイトから送られたものは受けない
$origin = $_SERVER['HTTP_ORIGIN'] ?? '';
$host = preg_replace('/:\d+$/', '', $_SERVER['HTTP_HOST'] ?? '');
if ($origin !== '' && parse_url($origin, PHP_URL_HOST) !== $host) {
    fail(403, 'このページからは送れません。');
}

// 機械による自動送信をはじく：人には見えない欄が埋まっている、または画面を開いてすぐ送っている
if (field('website', 200) !== '') { header('Location: thanks/', true, 303); exit; }
$elapsed = (int)field('e', 20);
if ($elapsed < 3000) fail(400, '送信が早すぎました。少し待ってから、もう一度「送る」を押してください。');

$kind = field('kind', 40);
$name = field('name', 60);
$email = field('email', 254);
$body = field('body', 4000);

if (!in_array($kind, KINDS, true)) fail(400, 'お問い合わせの種類を選んでください。');
if ($email === '' || !filter_var($email, FILTER_VALIDATE_EMAIL)) fail(400, 'メールアドレスを正しく入れてください。');
if (mb_strlen($body) < 5) fail(400, 'お問い合わせの内容を入れてください。');
if (preg_match_all('#https?://#i', $body) > 3) fail(400, 'URL が多すぎます。3つまでにしてください。');

$to = @file_get_contents("$base/contact_to.txt");
$to = $to === false ? '' : trim($to);
if (!filter_var($to, FILTER_VALIDATE_EMAIL)) fail(503, 'ただいまお問い合わせを受け付けていません。時間をおいてお試しください。');

// 送りすぎを防ぐ（記録は公開フォルダの外に、IP アドレスは元に戻せない形で残す）
$dir = "$base/contact-log";
if (!is_dir($dir)) @mkdir($dir, 0700, true);
$now = time();
$ipkey = hash('sha256', ($_SERVER['REMOTE_ADDR'] ?? '') . '|' . __DIR__);
$logf = "$dir/" . date('Ymd') . '.log';
$fp = @fopen($logf, 'c+');
if ($fp) {
    flock($fp, LOCK_EX);
    $lines = array_filter(explode("\n", stream_get_contents($fp)));
    if (count($lines) >= PER_DAY) fail(429, '本日はお問い合わせが多いため、受付を止めています。明日もう一度お試しください。');
    $mine = 0;
    foreach ($lines as $l) {
        [$t, $k] = array_pad(explode(' ', $l, 2), 2, '');
        if ($k === $ipkey && $now - (int)$t < 600) $mine++;
    }
    if ($mine >= PER_IP_10MIN) fail(429, '続けて送られています。10分ほどたってから、もう一度お試しください。');
    fwrite($fp, "$now $ipkey\n");
    flock($fp, LOCK_UN);
    fclose($fp);
    foreach (glob("$dir/*.log") ?: [] as $old) {
        if (filemtime($old) < $now - 3 * 86400) @unlink($old);
    }
}

$subject = '【' . SITE_NAME . '】お問い合わせ：' . $kind;
$text = "ジモふくのお問い合わせフォームから届きました。\n"
    . "このメールに返信すると、送った方に届きます。\n\n"
    . "種類：$kind\n"
    . 'お名前：' . ($name !== '' ? $name : '（なし）') . "\n"
    . "メール：$email\n"
    . '日時：' . date('Y年n月j日 H:i') . "\n"
    . "──────────\n$body\n──────────\n";
$headers = 'From: ' . mb_encode_mimeheader(SITE_NAME . ' お問い合わせ') . ' <' . FROM . ">\r\n"
    . "Reply-To: $email";
if (!mb_send_mail($to, $subject, $text, $headers, '-f' . FROM)) {
    fail(500, 'メールの送信に失敗しました。時間をおいて、もう一度お試しください。');
}
header('Location: thanks/', true, 303);
