<?php
// 地域レポートの受け取りの申し込み。入力を jimofuku.jp/leads/leads.csv に残し、ダウンロード用のリンクをメールで送る。
require __DIR__ . '/common.php';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') { header('Location: ./', true, 303); exit; }
same_site();

// 機械による自動送信をはじく
if (field('website', 200) !== '') { header('Location: sent/', true, 303); exit; }
if ((int)field('e', 20) < 3000) fail(400, '送信が早すぎました。少し待ってから、もう一度「メールで受け取る」を押してください。');

$cat = catalog();
$city = field('city', 40);
$email = field('email', 254);
$role = field('role', 40);
$org = field('org', 100);
$news = field('news', 1) === '1' ? 'はい' : 'いいえ';
$eds = isset($_POST['ed']) && is_array($_POST['ed']) ? array_values(array_unique(array_filter($_POST['ed'], 'is_string'))) : [];

if (!isset($cat['cities'][$city])) fail(400, '市区を選んでください。');
$okEds = $cat['cities'][$city]['eds'] ?? array_keys($cat['editions']);   // リクエストで作った市区は、作った版だけ
$eds = array_values(array_filter($eds, fn($k) => isset($cat['editions'][$k]) && in_array($k, $okEds, true)));
if (!$eds) fail(400, '版を1つ以上選んでください。');
if ($email === '' || !filter_var($email, FILTER_VALIDATE_EMAIL)) fail(400, 'メールアドレスを正しく入れてください。');
if (!in_array($role, $cat['roles'], true)) fail(400, 'お立場を選んでください。');

rate_limit('dl', 5, 600, 300);

// ダウンロード用のしるし（推測できない文字列）
if (!is_dir("$LEADS/tokens")) @mkdir("$LEADS/tokens", 0700, true);
$token = bin2hex(random_bytes(16));
$rec = ['email' => $email, 'city' => $city, 'eds' => $eds, 't' => time()];
if (@file_put_contents("$LEADS/tokens/$token.json", json_encode($rec, JSON_UNESCAPED_UNICODE)) === false) {
    fail(503, 'ただいま受け付けられません。時間をおいてお試しください。');
}
foreach (glob("$LEADS/tokens/*.json") ?: [] as $old) {
    if (filemtime($old) < time() - (TOKEN_DAYS + 1) * 86400) @unlink($old);
}

// リストに残す（Excel で開けるよう、はじめに BOM をつける）
$names = array_map(fn($k) => $cat['editions'][$k]['name'], $eds);
$csv = "$LEADS/leads.csv";
$new = !file_exists($csv);
$fp = @fopen($csv, 'a');
if ($fp) {
    flock($fp, LOCK_EX);
    if ($new) { fwrite($fp, "\xEF\xBB\xBF"); fputcsv($fp, ['日時', 'メールアドレス', 'お立場', '法人・団体名', '都道府県', '市区', '版', 'お知らせ']); }
    $safe = fn($v) => preg_match('/^[=+\-@]/', $v) ? "'" . $v : $v;   // Excel で式として動かないように
    fputcsv($fp, [date('Y-m-d H:i'), $safe($email), $role, $safe($org), $cat['cities'][$city]['pref'], $city, implode('・', $names), $news]);
    flock($fp, LOCK_UN);
    fclose($fp);
    @chmod($csv, 0600);
}

$link = SITE_URL . '/report/area/get.php?t=' . $token;
$text = "{$city}の地域レポートのお申し込みありがとうございます。\n\n"
    . "下のリンクを開くと、PDF をダウンロードできます（" . TOKEN_DAYS . "日間使えます）。\n$link\n\n"
    . "お申し込みの内容\n・市区：{$city}\n・版：" . implode('、', $names) . "\n\n"
    . ($news === 'はい' ? "新しいレポートや、データの更新のお知らせをこのアドレスにお送りします。不要になったときは、お知らせのメールに書いてある方法で止められます。\n" : '')
    . "レポートの数字や図は、出典（ジモふく）を明記すれば資料などに引用できます。PDF そのものの再配布はご遠慮ください。\n"
    . mail_footer();
if (!send_mail($email, '【' . SITE_NAME . '】' . $city . 'の地域レポートのダウンロード', $text)) {
    fail(500, 'メールを送れませんでした。メールアドレスをお確かめのうえ、もう一度お試しください。');
}

// 運営者へのお知らせ
$to = owner_to();
if ($to !== '') {
    send_mail($to, '【' . SITE_NAME . '】レポートの申し込み：' . $city . '（' . $role . '）',
        "地域レポートの申し込みがありました。\n\n日時：" . date('Y年n月j日 H:i') . "\nメール：$email\nお立場：$role\n法人・団体名：" . ($org !== '' ? $org : '（なし）')
        . "\n市区：$city\n版：" . implode('、', $names) . "\nお知らせ：$news\n\nこれまでの一覧は " . SITE_URL . "/report/area/list.php から、このアドレスに送れます。\n", $email);
}
header('Location: sent/', true, 303);
