"""WAM NET オープンデータのサービス種別の定義。

コードは WAM NET の ZIP ファイル名（sfkopendata_YYYYMM_<コード>.zip）に対応する。
slug はページの URL に使うので、一度公開したら変えない。
"""

GROUPS = [
    ("訪問系", ["11", "12", "15", "13", "14"]),
    ("日中活動系", ["22", "21", "24"]),
    ("施設系", ["32"]),
    ("居住系", ["33", "61"]),
    ("訓練・就労系", ["46", "45", "60", "62", "42", "41", "34"]),
    ("障害児通所系", ["65", "63", "67", "66", "64"]),
    ("障害児入所系", ["68", "69"]),
    ("相談系", ["52", "70", "53", "54"]),
]

# code: (正式名, 短い呼び名, slug)
SERVICES = {
    "11": ("居宅介護", "居宅介護", "kyotaku-kaigo"),
    "12": ("重度訪問介護", "重度訪問介護", "judo-homon-kaigo"),
    "15": ("同行援護", "同行援護", "doko-engo"),
    "13": ("行動援護", "行動援護", "kodo-engo"),
    "14": ("重度障害者等包括支援", "重度包括支援", "judo-hokatsu"),
    "21": ("療養介護", "療養介護", "ryoyo-kaigo"),
    "22": ("生活介護", "生活介護", "seikatsu-kaigo"),
    "24": ("短期入所", "短期入所", "tanki-nyusho"),
    "32": ("施設入所支援", "施設入所支援", "shisetsu-nyusho"),
    "33": ("共同生活援助", "グループホーム", "group-home"),
    "61": ("自立生活援助", "自立生活援助", "jiritsu-seikatsu-enjo"),
    "41": ("自立訓練（機能訓練）", "機能訓練", "kino-kunren"),
    "42": ("自立訓練（生活訓練）", "生活訓練", "seikatsu-kunren"),
    "34": ("宿泊型自立訓練", "宿泊型自立訓練", "shukuhaku-kunren"),
    "60": ("就労移行支援", "就労移行支援", "shuro-iko"),
    "45": ("就労継続支援A型", "就労A型", "shuro-a"),
    "46": ("就労継続支援B型", "就労B型", "shuro-b"),
    "62": ("就労定着支援", "就労定着支援", "shuro-teichaku"),
    "63": ("児童発達支援", "児童発達支援", "jido-hattatsu"),
    "64": ("医療型児童発達支援", "医療型児童発達支援", "iryo-jido-hattatsu"),
    "65": ("放課後等デイサービス", "放課後等デイ", "houkago-day"),
    "66": ("居宅訪問型児童発達支援", "居宅訪問型児発", "kyotaku-homon-jido"),
    "67": ("保育所等訪問支援", "保育所等訪問支援", "hoikusho-homon"),
    "68": ("福祉型障害児入所施設", "福祉型障害児入所", "fukushi-shogaiji-nyusho"),
    "69": ("医療型障害児入所施設", "医療型障害児入所", "iryo-shogaiji-nyusho"),
    "53": ("地域移行支援", "地域移行支援", "chiiki-iko"),
    "54": ("地域定着支援", "地域定着支援", "chiiki-teichaku"),
    "52": ("計画相談支援", "計画相談支援", "keikaku-sodan"),
    "70": ("障害児相談支援", "障害児相談支援", "shogaiji-sodan"),
}

PREFS = (
    "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 "
    "東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 "
    "滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 "
    "香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県"
).split()

PREF_SLUGS = (
    "hokkaido aomori iwate miyagi akita yamagata fukushima ibaraki tochigi gunma saitama chiba "
    "tokyo kanagawa niigata toyama ishikawa fukui yamanashi nagano gifu shizuoka aichi mie "
    "shiga kyoto osaka hyogo nara wakayama tottori shimane okayama hiroshima yamaguchi tokushima "
    "kagawa ehime kochi fukuoka saga nagasaki kumamoto oita miyazaki kagoshima okinawa"
).split()
