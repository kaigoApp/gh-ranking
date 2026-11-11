"""とうきょう福祉ナビゲーションの「評価結果ページ」へのリンクを作る。

福ナビは、検索結果の一覧から事業所名を押したときに送る項目が（空の値も含めて）ほぼ全部そろっていないと、
エラーページ（contents/others/error.html）に飛ばす。そのため、一覧画面の showDetail() が実際に送る項目を
同じ順番でそのまま並べる（2026-11-11 にブラウザで確かめた形）。
build.py（リンクを出す）と fetch_hyoka.py（毎月、リンクが開けるか確かめる）の両方で使う。
"""
import urllib.parse

BASE = "https://www.fukunavi.or.jp/fukunavi/controller?"

# (項目名, 値)。値が None のところに事業所ごとの値を入れる
_TEMPLATE = [
    ("actionID", "hyk"), ("cmd", "hyklstdtldigest"), ("BEF_PRC", "hyk"), ("HYK_ID", None),
    *[(f"HYK_ID{i}", "") for i in range(1, 6)], *[(f"JGY_CD{i}", "") for i in range(1, 6)],
    ("SCHSVCSBRCD", ""), ("SVCDBRCD", ""), ("PTN_CD", ""), ("SVCSBRCDALL", ""), ("SVCSBRCD", None),
    ("AREA1", ""), ("AREA2", ""), ("AREA3", ""), ("HYK_YR", ""), ("SCHHYK_YR", ""), ("NAME", ""),
    ("JGY_CD", None), ("MODE", "multi"), ("DVS_CD", ""), ("SVCDBR_CD", "23"), ("SVCSBR_CD", ""), ("ROW", "0"),
    ("FROMDT", ""), ("SCH_ACTION", "hyklst"), ("KOHYO", ""), ("GEN", ""), ("HYKNEN", ""), ("LISTSVC", ""),
    ("ORDER", ""), ("HYK_DTL_CHK", ""), ("PRMCMT_CHK", ""), ("HYK_CHK", ""), ("JGY_CHK", ""), ("SVC_CHK", ""),
    ("DIG_MOVE_FLG", ""), *[(f"MLT_SVCSBR_CD{i}", "") for i in range(1, 9)],
    ("COLOR_FLG", ""), ("COLOR_HYK_ID", ""), ("BEFORE_FLG", ""), *[(f"MLT_DTL_SVCSBR_CD{i}", "") for i in range(1, 9)],
    ("HIKAKU_SVCSBRCD", ""), *[(f"TELOPN00{a}_NO{b}", "") for a in (1, 2, 3) for b in (1, 2, 3)],
    ("S_MODE", "service"), ("MLT_AREA", "3"), ("H_NAME", ""), ("J_NAME", ""), ("SVCDBR_CD", "23"),
    ("STEP_SVCSBRCD", None),
]


def hyoka_url(hyk_id: str, svc: str, jgy_cd: str) -> str:
    """hyk_id: 評価の番号、svc: 福ナビのサービスの番号（342 など）、jgy_cd: 福ナビの事業所の番号"""
    fill = {"HYK_ID": hyk_id, "SVCSBRCD": svc, "JGY_CD": jgy_cd, "STEP_SVCSBRCD": svc}
    return BASE + urllib.parse.urlencode([(k, fill[k] if v is None else v) for k, v in _TEMPLATE])
