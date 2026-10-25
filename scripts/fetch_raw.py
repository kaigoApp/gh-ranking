"""data/report/raw_urls.txt の資料を取りに行き、文字にして data/report/raw/<名前>.txt に書く。
このコンテナからは市・区のサイトにつながらないので、GitHub Actions（report-data.yml）で動かす。
PDF は pdftotext -layout、Excel は openpyxl でシートごとにタブ区切りにする。
"""
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

import openpyxl

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "report" / "raw"


def main():
    OUT.mkdir(exist_ok=True)
    for line in (ROOT / "data" / "report" / "raw_urls.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, url = line.split()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=120).read()
        except Exception as e:  # 取れなかった資料は名前だけ残す
            (OUT / f"{name}.txt").write_text(f"ERROR {url}\n{e}\n", encoding="utf-8")
            print("NG", name, e, file=sys.stderr)
            continue
        with tempfile.TemporaryDirectory() as d:
            kind = "xlsx" if data[:2] == b"PK" else "xls" if data[:4] == b"\xd0\xcf\x11\xe0" else "pdf"
            src = pathlib.Path(d) / ("f." + kind)
            src.write_bytes(data)
            if kind == "xls":
                import xlrd
                wb = xlrd.open_workbook(src)
                lines = []
                for sh in wb.sheets():
                    lines.append(f"=== {sh.name}")
                    for i in range(sh.nrows):
                        v = sh.row_values(i)
                        if any(x != "" for x in v):
                            lines.append("\t".join(str(x) for x in v))
                text = "\n".join(lines)
            elif kind == "xlsx":
                wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
                lines = []
                for ws in wb.worksheets:
                    lines.append(f"=== {ws.title}")
                    for row in ws.iter_rows(values_only=True):
                        if any(v is not None for v in row):
                            lines.append("\t".join("" if v is None else str(v) for v in row))
                text = "\n".join(lines)
            else:
                text = subprocess.run(["pdftotext", "-layout", str(src), "-"], capture_output=True,
                                      text=True).stdout
        (OUT / f"{name}.txt").write_text(f"{url}\n{text}", encoding="utf-8")
        print("OK", name, len(data))


if __name__ == "__main__":
    main()
