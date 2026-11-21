<?php
// 運営者向け：これまでの申し込みの一覧（CSV）を、運営者のアドレス（contact_to.txt）にメールで送る。
// 送り先は運営者だけなので、だれが押しても一覧がほかの人に渡ることはない。
require __DIR__ . '/common.php';

$csv = "$LEADS/leads.csv";
$n = is_file($csv) ? max(0, count(file($csv, FILE_SKIP_EMPTY_LINES)) - 1) : 0;

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    page_out(200, '申し込みの一覧を送る',
        '<p>地域レポートの申し込みの一覧（Excel で開ける CSV）を、運営者のメールアドレスに送ります。</p>'
        . '<form method="post"><button class="btn" type="submit">一覧をメールで送る</button></form>');
}
same_site();
$to = owner_to();
if ($to === '') fail(503, '送り先のアドレスが設定されていません。');
// 10分に1回まで
$stamp = "$LEADS/list_sent.txt";
if (is_file($stamp) && time() - filemtime($stamp) < 600) fail(429, '少し前に送りました。メールが届いていないか確かめてください（10分たつと、もう一度送れます）。');
@touch($stamp);
$data = is_file($csv) ? (string)file_get_contents($csv) : "\xEF\xBB\xBF日時,メールアドレス,お立場,法人・団体名,都道府県,市区,版,お知らせ\n";
$ok = send_mail($to, '【' . SITE_NAME . '】レポートの申し込み一覧（' . date('Y年n月j日') . '時点・' . $n . '件）',
    "地域レポートの申し込みの一覧です（{$n}件）。添付の CSV は Excel でそのまま開けます。\n\n"
    . "「お知らせ」が「はい」の方だけに、新しいレポートなどのお知らせを送れます（「いいえ」の方には送らないでください）。\n",
    '', ['jimofuku_leads_' . date('Ymd') . '.csv' => $data]);
if (!$ok) fail(500, 'メールの送信に失敗しました。');
page_out(200, '送りました', '<p>' . h((string)$n) . '件の一覧を、運営者のメールアドレスに送りました。</p>');
