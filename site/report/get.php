<?php
// メールのリンクから開く、地域レポートの PDF のダウンロード。
require __DIR__ . '/common.php';

$t = isset($_GET['t']) && is_string($_GET['t']) ? $_GET['t'] : '';
if (!preg_match('/^[0-9a-f]{32}$/', $t)) fail(404, 'リンクが正しくありません。メールに書かれたリンクをそのまま開いてください。');
$rec = json_decode((string)@file_get_contents("$LEADS/tokens/$t.json"), true);
if (!is_array($rec)) fail(404, 'リンクが見つかりません。お手数ですが、地域レポートのページからもう一度お申し込みください。');
if (time() - (int)$rec['t'] > TOKEN_DAYS * 86400) fail(410, 'リンクの期限（' . TOKEN_DAYS . '日）が過ぎました。お手数ですが、地域レポートのページからもう一度お申し込みください。');

$cat = catalog();
$city = $rec['city'];
if (!isset($cat['cities'][$city])) fail(404, 'このレポートは公開を終了しました。');
$c = $cat['cities'][$city];

$f = isset($_GET['f']) && is_string($_GET['f']) ? $_GET['f'] : '';
if ($f !== '') {
    if (!in_array($f, $rec['eds'], true) || !isset($cat['editions'][$f])) fail(404, 'このリンクでは、その版はダウンロードできません。');
    $ed = $cat['editions'][$f];
    $path = "$PDFS/{$c['file']}_{$ed['file']}.pdf";
    if (!is_file($path)) fail(503, 'ただいまファイルを用意しています。時間をおいてお試しください。');
    @file_put_contents("$LEADS/downloads.log", date('Y-m-d H:i') . "\t$t\t$city\t{$ed['name']}\n", FILE_APPEND | LOCK_EX);
    $name = "ジモふく地域レポート_{$city}_{$ed['name']}.pdf";
    header('Content-Type: application/pdf');
    header("Content-Disposition: attachment; filename=\"{$c['file']}_{$ed['file']}.pdf\"; filename*=UTF-8''" . rawurlencode($name));
    header('Content-Length: ' . filesize($path));
    header('X-Robots-Tag: noindex');
    readfile($path);
    exit;
}

$items = '';
foreach ($rec['eds'] as $k) {
    if (!isset($cat['editions'][$k])) continue;
    $items .= '<li style="margin:10px 0"><a class="btn" href="get.php?t=' . h($t) . '&amp;f=' . h($k) . '">' . h($cat['editions'][$k]['name']) . '（PDF）をダウンロード</a></li>';
}
page_out(200, h($city) . 'の地域レポート',
    '<p>下のボタンを押すと PDF をダウンロードできます。このページはメールのリンクから' . TOKEN_DAYS . '日間開けます。</p>'
    . '<ul style="list-style:none;padding:0">' . $items . '</ul>'
    . '<p class="note">数字や図は、出典（ジモふく）を明記すれば資料などに引用できます。PDF そのものの再配布はご遠慮ください。</p>');
