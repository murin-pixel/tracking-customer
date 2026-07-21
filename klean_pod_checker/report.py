from __future__ import annotations

import csv
import html
import json
import os
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import JobResult, OrderRef


ReportRow = tuple[OrderRef, JobResult]


def write_reports(
    rows: list[ReportRow], output_dir: Path, *, keep_history: bool = True
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    latest_csv = output_dir / "latest.csv"
    latest_html = output_dir / "latest.html"

    _write_csv(latest_csv, rows)
    _write_html(latest_html, rows)
    paths = {"latest_csv": latest_csv, "latest_html": latest_html}

    if keep_history:
        history_csv = output_dir / f"status-{timestamp}.csv"
        history_html = output_dir / f"status-{timestamp}.html"
        _write_csv(history_csv, rows)
        _write_html(history_html, rows)
        paths.update({"history_csv": history_csv, "history_html": history_html})
    return paths


def _write_csv(path: Path, rows: list[ReportRow]) -> None:
    with _atomic_text(path, encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "sheet_rows",
                "order_number",
                "carrier",
                "order_date",
                "found",
                "status_code",
                "status_th",
                "status_en",
                "delivered",
                "driver",
                "group_name",
                "created_at",
                "delivery_at",
                "updated_at",
                "proof_count",
                "proof_urls",
                "checked_at",
                "error",
            ],
        )
        writer.writeheader()
        for ref, result in rows:
            proof_urls, _ = _display_proof_urls(ref.carrier, result.proof_urls)
            writer.writerow(
                {
                    "sheet_rows": ",".join(map(str, ref.sheet_rows)),
                    "order_number": ref.order_number,
                    "carrier": ref.carrier,
                    "order_date": ref.order_date.isoformat() if ref.order_date else "",
                    "found": result.found,
                    "status_code": result.status_code,
                    "status_th": result.status_th,
                    "status_en": result.status_en,
                    "delivered": result.delivered,
                    "driver": result.driver,
                    "group_name": result.group_name,
                    "created_at": result.created_at,
                    "delivery_at": result.delivery_at,
                    "updated_at": result.updated_at,
                    "proof_count": len(proof_urls),
                    "proof_urls": "\n".join(proof_urls),
                    "checked_at": result.checked_at,
                    "error": result.error,
                }
            )


def _write_html(path: Path, rows: list[ReportRow]) -> None:
    counts = Counter(_bucket(result) for _, result in rows)
    checked = max((result.checked_at for _, result in rows), default="-")
    body_rows = "\n".join(_html_row(ref, result) for ref, result in rows)
    report_json = _safe_report_json(rows)
    actionable = len(rows) - counts["delivered"]
    document = f"""<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Delivery Status · BeDee Fulfilment</title>
<style>
:root {{ color-scheme: light; font-family: system-ui, -apple-system, "Noto Sans Thai", sans-serif; --green:#151a98; --blue:#1182f2; --ink:#171c37; --muted:#68728a; --line:#dfe5f0; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #fff; color: var(--ink); }}
button,input {{ font: inherit; }}
.sr-only {{ position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0; }}
main {{ max-width: 1500px; margin: auto; padding: 24px; }}
h1 {{ margin: 0 0 6px; }} .muted {{ color: var(--muted); }}
.report-brand {{ display:flex;align-items:center;gap:24px;margin-bottom:20px;padding:8px 0 4px; }}
.report-brand img {{ display:block;width:210px;height:auto;flex:0 0 auto; }}
.report-brand h1 {{ font-size:clamp(25px,3vw,38px);line-height:1.2; }}
.cards {{ display: grid; grid-template-columns: repeat(5,minmax(120px,1fr)); gap: 12px; margin: 22px 0; }}
.card {{ background: white; border:1px solid var(--line); border-radius: 12px; padding: 15px; box-shadow: 0 6px 18px #151a980d; }}
.card strong {{ display: block; font-size: 26px; }}
.attention {{ display: flex; align-items: center; justify-content: space-between; gap: 18px; margin: 0 0 14px; padding: 13px 16px; border: 1px solid #f0d7a7; border-radius: 12px; background: #fff8e9; color: #754600; }}
.attention strong {{ display: block; }} .attention span {{ font-size: 13px; }}
.toolbar {{ position: sticky; top: 0; z-index: 5; padding: 14px; border-bottom: 1px solid var(--line); background: #fffffff2; backdrop-filter: blur(8px); }}
.toolbar-top {{ display: grid; grid-template-columns: minmax(260px,1fr) auto auto; gap: 12px; align-items: center; }}
.report-search {{ height: 44px; width: 100%; padding: 0 14px; border: 1px solid #cbd6e8; border-radius: 10px; outline: none; }}
.report-search:focus {{ border-color: var(--blue); box-shadow: 0 0 0 3px #1182f224; }}
.visible-count {{ color: var(--muted); font-size: 13px; white-space: nowrap; }}
.filter-row {{ display:flex;align-items:center;gap:10px;margin-top:10px; }}
.filter-label {{ flex:0 0 auto;color:var(--muted);font-size:12px;font-weight:800; }}
.filters {{ display: flex; gap: 7px; overflow-x: auto; padding-bottom: 2px; }}
.filter-button {{ border: 1px solid #cbd6e8; border-radius: 99px; padding: 7px 11px; background: white; color: #4f5c78; cursor: pointer; white-space: nowrap; font-size: 13px; }}
.filter-button.active {{ border-color: var(--green); background: #eaf3ff; color: var(--green); font-weight: 700; }}
.export-button {{ min-height:40px;border:0;border-radius:9px;padding:0 15px;background:var(--green);color:white;font-weight:800;cursor:pointer;white-space:nowrap; }}
.export-button:hover {{ background:#0d1277; }} .export-button:disabled {{ opacity:.55;cursor:not-allowed; }}
.table-wrap {{ overflow: auto; background: white; border:1px solid var(--line); border-radius: 12px; box-shadow: 0 6px 18px #151a980d; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th {{ position: sticky; top: 164px; z-index: 3; background: #eaf3ff; text-align: left; white-space: nowrap; }}
th,td {{ border-bottom: 1px solid #e6ecea; padding: 10px 12px; vertical-align: top; }}
.ok {{ color: #151a98; font-weight: 700; }} .wait {{ color: #9a5b00; font-weight: 700; }}
.bad {{ color: #b3261e; font-weight: 700; }} .missing {{ color: #6d6875; font-weight: 700; }}
a {{ color: #0669a8; }} code {{ white-space: nowrap; }}
.row-delivered {{ background: #fbfdff; }} .row-pending {{ background: #fffdf8; }}
.row-rejected,.row-error {{ background: #fff9f8; }} .row-missing {{ background: #fafafa; }}
.order-button {{ display: block; border: 0; padding: 0; background: transparent; color: #151a98; font-weight: 800; cursor: pointer; text-align: left; }}
.mini-action {{ margin-top: 5px; border: 0; padding: 0; background: transparent; color: #667871; font-size: 11px; cursor: pointer; }}
.mini-action:hover,.order-button:hover {{ text-decoration: underline; }}
.refresh-button {{ border: 1px solid #bdcbed; border-radius: 8px; padding: 7px 10px; background: white; color: #151a98; font-weight: 700; cursor: pointer; white-space: nowrap; }}
.refresh-button:disabled {{ opacity: .55; cursor: wait; }}
.age {{ display: block; margin-top: 5px; color: var(--muted); font-size: 11px; font-weight: 400; }}
.age.stale {{ color: #b3261e; font-weight: 700; }}
.proofs {{ min-width: 190px; }}
.proof-list {{ display: grid; grid-template-columns: repeat(3, 56px); gap: 7px; }}
.proof-thumb {{ position: relative; display: block; width: 56px; height: 56px; overflow: hidden; border: 1px solid #cbd6e8; border-radius: 8px; background: #edf2f8; }}
.proof-thumb img {{ display: block; width: 100%; height: 100%; object-fit: cover; }}
.proof-thumb span {{ position: absolute; right: 3px; bottom: 3px; min-width: 17px; padding: 1px 4px; border-radius: 9px; background: #171c37dd; color: white; font-size: 10px; line-height: 15px; text-align: center; }}
.proof-thumb:hover {{ border-color: #1182f2; box-shadow: 0 2px 8px #151a9830; }}
.empty-table {{ padding: 34px; text-align: center; color: var(--muted); }}
dialog {{ width: min(760px,calc(100% - 28px)); max-height: calc(100vh - 28px); padding: 0; border: 0; border-radius: 18px; color: var(--ink); box-shadow: 0 24px 80px #151a9852; }}
dialog::backdrop {{ background: #10153875; backdrop-filter: blur(3px); }}
.dialog-shell {{ padding: 24px; }} .dialog-header {{ display:flex;justify-content:space-between;gap:18px;align-items:flex-start;border-bottom:1px solid var(--line);padding-bottom:18px; }}
.dialog-header h2 {{ margin: 3px 0 0; overflow-wrap:anywhere; }} .dialog-close {{ border:0;background:#edf2f8;border-radius:50%;width:36px;height:36px;cursor:pointer;font-size:20px; }}
.dialog-status {{ display:inline-block;margin-top:12px;padding:7px 11px;border-radius:99px;background:#eaf3ff;font-size:13px;font-weight:800; }}
.detail-grid {{ display:grid;grid-template-columns:repeat(3,1fr);margin:0; }} .detail-grid div {{ padding:17px 14px 17px 0;border-bottom:1px solid var(--line); }}
.detail-grid dt {{ color:var(--muted);font-size:12px;margin-bottom:5px; }} .detail-grid dd {{ margin:0;font-weight:650;overflow-wrap:anywhere; }}
.dialog-proofs {{ padding-top:18px; }} .dialog-proofs h3 {{ margin:0 0 12px; }} .dialog-proof-grid {{ display:grid;grid-template-columns:repeat(4,1fr);gap:10px; }}
.dialog-proof {{ overflow:hidden;border:1px solid var(--line);border-radius:10px;text-decoration:none; }} .dialog-proof img {{ display:block;width:100%;aspect-ratio:4/3;object-fit:cover;background:#edf2f8; }} .dialog-proof span {{ display:block;padding:8px;color:#151a98;font-size:12px;font-weight:700; }}
.dialog-actions {{ display:flex;flex-wrap:wrap;gap:9px;padding-top:20px; }} .dialog-action {{ border:1px solid #bdcbed;border-radius:9px;padding:9px 12px;background:white;color:#151a98;font-weight:700;cursor:pointer; }} .dialog-action.primary {{ border-color:#151a98;background:#151a98;color:white; }}
.dialog-note {{ min-height:20px;margin:12px 0 0;color:var(--muted);font-size:13px; }}
@media (max-width: 760px) {{ main {{ padding: 14px; }} .report-brand {{ align-items:flex-start;flex-direction:column;gap:12px; }} .report-brand img {{ width:180px; }} .cards {{ grid-template-columns: repeat(2,1fr); }} .toolbar-top {{ grid-template-columns:1fr; }} .export-button {{ width:100%; }} .filter-row {{ align-items:flex-start;flex-direction:column; }} th {{ top:258px; }} .attention {{ align-items:flex-start;flex-direction:column; }} .detail-grid {{ grid-template-columns:1fr 1fr; }} .dialog-proof-grid {{ grid-template-columns:repeat(2,1fr); }} }}
</style>
<script defer src="/static/report.js?v=20260717d"></script>
</head>
<body><main>
<header class="report-brand"><img src="/static/bedee-logo.png?v=20260717" alt="BeDee Powered by BDMS"><div><h1>สถานะจัดส่ง KLEAN&amp;KARE, KEX และ InterExpress</h1><div class="muted">ตรวจล่าสุด {html.escape(checked)} · ออเดอร์ไม่ซ้ำ {len(rows)} รายการ</div></div></header>
<section class="cards">
  <div class="card"><span>จัดส่งสำเร็จ</span><strong>{counts['delivered']}</strong></div>
  <div class="card"><span>กำลังดำเนินการ</span><strong>{counts['pending']}</strong></div>
  <div class="card"><span>ปฏิเสธ/ไม่สำเร็จ</span><strong>{counts['rejected']}</strong></div>
  <div class="card"><span>ไม่พบ</span><strong>{counts['missing']}</strong></div>
  <div class="card"><span>ผิดพลาด</span><strong>{counts['error']}</strong></div>
</section>
<section class="attention" id="attention-banner"><div><strong>มี {actionable} รายการที่ต้องติดตาม</strong><span id="attention-detail">แสดงเฉพาะงานที่ยังไม่จบเป็นค่าเริ่มต้น</span></div><button class="refresh-button" type="button" data-filter-jump="actionable">ดูรายการ</button></section>
<div class="table-wrap">
<div class="toolbar">
  <div class="toolbar-top"><label><span class="sr-only">ค้นหารายงาน</span><input id="report-search" class="report-search" type="search" placeholder="ค้นหาเลขออเดอร์หรือคนขับ"></label><span id="visible-count" class="visible-count"></span><button id="export-visible" class="export-button" type="button">Export CSV</button></div>
  <div class="filter-row"><span class="filter-label">สถานะ</span><div class="filters" role="group" aria-label="กรองสถานะ">
      <button class="filter-button active" type="button" data-filter="actionable">ต้องติดตาม ({actionable})</button>
      <button class="filter-button" type="button" data-filter="all">ทั้งหมด ({len(rows)})</button>
      <button class="filter-button" type="button" data-filter="pending">กำลังดำเนินการ ({counts['pending']})</button>
      <button class="filter-button" type="button" data-filter="delivered">สำเร็จ ({counts['delivered']})</button>
      <button class="filter-button" type="button" data-filter="missing">ไม่พบ ({counts['missing']})</button>
      <button class="filter-button" type="button" data-filter="rejected">ไม่สำเร็จ ({counts['rejected']})</button>
  </div></div>
  <div class="filter-row"><span class="filter-label">ขนส่ง</span><div class="filters" role="group" aria-label="กรองขนส่ง">
      <button class="filter-button active" type="button" data-carrier="all">ทั้งหมด</button>
      <button class="filter-button" type="button" data-carrier="skyfrog">KLEAN&amp;KARE</button>
      <button class="filter-button" type="button" data-carrier="kex">KEX</button>
      <button class="filter-button" type="button" data-carrier="interexpress">InterExpress</button>
  </div>
  </div>
</div>
<table id="report-table">
<thead><tr><th>แถวในชีต</th><th>ผู้ขนส่ง</th><th>เลขออเดอร์/พัสดุ</th><th>วันที่จากเลขงาน</th><th>สถานะ</th><th>คนขับ</th><th>วันส่ง</th><th>หลักฐาน</th><th>ดำเนินการ</th></tr></thead>
<tbody>{body_rows}</tbody>
</table><div id="empty-table" class="empty-table" hidden>ไม่พบรายการที่ตรงกับเงื่อนไข</div></div>

<dialog id="order-dialog">
  <div class="dialog-shell">
    <div class="dialog-header"><div><span class="muted">รายละเอียดออเดอร์</span><h2 id="dialog-order">—</h2><span id="dialog-status" class="dialog-status">—</span></div><button class="dialog-close" type="button" data-dialog-close aria-label="ปิด">×</button></div>
    <dl class="detail-grid">
      <div><dt>คนขับ</dt><dd id="dialog-driver">—</dd></div><div><dt>กลุ่มงาน</dt><dd id="dialog-group">—</dd></div>
      <div><dt>วันสร้างงาน</dt><dd id="dialog-created">—</dd></div><div><dt>วันจัดส่ง</dt><dd id="dialog-delivery">—</dd></div><div><dt>ตรวจล่าสุด</dt><dd id="dialog-checked">—</dd></div>
    </dl>
    <section class="dialog-proofs"><h3>รูปหลักฐาน</h3><div id="dialog-proof-grid" class="dialog-proof-grid"></div></section>
    <div class="dialog-actions"><button id="dialog-refresh" class="dialog-action primary" type="button">ตรวจใหม่</button><button id="copy-order" class="dialog-action" type="button">คัดลอกเลขออเดอร์</button><button id="copy-proofs" class="dialog-action" type="button">คัดลอกลิงก์ POD</button><button id="copy-message" class="dialog-action" type="button">คัดลอกข้อความแจ้งลูกค้า</button></div>
    <p id="dialog-note" class="dialog-note"></p>
  </div>
</dialog>
<script id="report-data" type="application/json">{report_json}</script>
</main></body></html>"""
    with _atomic_text(path, encoding="utf-8", newline="") as file:
        file.write(document)


def _html_row(ref: OrderRef, result: JobResult) -> str:
    bucket = _bucket(result)
    css = {
        "delivered": "ok",
        "pending": "wait",
        "rejected": "bad",
        "missing": "missing",
        "error": "bad",
    }[bucket]
    if result.error:
        status = f"ผิดพลาด: {result.error}"
    elif not result.found:
        status = "ไม่พบในระบบขนส่ง"
    else:
        status = f"{result.status_th} ({result.status_code})"
    proof_urls, first_proof_number = _display_proof_urls(ref.carrier, result.proof_urls)
    proof_content = _proof_html(proof_urls, first_proof_number)
    searchable = " ".join(
        [result.order_number, result.driver, result.group_name]
    ).casefold()
    order = html.escape(result.order_number, quote=True)
    checked = html.escape(result.checked_at, quote=True)
    return "".join(
        [
            f'<tr class="row-{bucket}" data-order="{order}" data-bucket="{bucket}" data-row-carrier="{html.escape(ref.carrier, quote=True)}" '
            f'data-checked="{checked}" data-search="{html.escape(searchable, quote=True)}">',
            f"<td>{html.escape(', '.join(map(str, ref.sheet_rows)))}</td>",
            f"<td>{_carrier_label(ref.carrier)}</td>",
            f'<td><button class="order-button" type="button" data-action="view" data-order="{order}"><code>{html.escape(ref.order_number)}</code></button><button class="mini-action" type="button" data-action="copy-order" data-order="{order}">คัดลอก</button></td>',
            f"<td>{ref.order_date.isoformat() if ref.order_date else '-'}</td>",
            f'<td class="row-status {css}">{html.escape(status)}<span class="age" data-age-for="{order}"></span></td>',
            f'<td class="row-driver">{html.escape(result.driver or "-")}</td>',
            f'<td class="row-delivery">{html.escape(result.delivery_at or "-")}</td>',
            f'<td class="proofs">{proof_content}</td>',
            f'<td><button class="refresh-button" type="button" data-action="refresh" data-order="{order}">ตรวจใหม่</button></td>',
            "</tr>",
        ]
    )


def _display_proof_urls(carrier: str, urls: list[str]) -> tuple[list[str], int]:
    """Exclude the two Skyfrog signature captures from report output."""
    if carrier == "skyfrog":
        return urls[2:], 3
    return urls, 1


def _proof_html(urls: list[str], first_proof_number: int = 1) -> str:
    links = "".join(
        (
            f'<a class="proof-thumb" href="{html.escape(url, quote=True)}" '
            f'target="_blank" rel="noreferrer" title="เปิดรูปหลักฐาน {index}">'
            f'<img src="{html.escape(url, quote=True)}" alt="รูปหลักฐาน {index}" '
            'loading="lazy" decoding="async">'
            f'<span>{index}</span></a>'
        )
        for index, url in enumerate(urls, start=first_proof_number)
    )
    return f'<div class="proof-list">{links}</div>' if links else "-"


def _safe_report_json(rows: list[ReportRow]) -> str:
    payload = []
    for ref, result in rows:
        item = asdict(result)
        item.pop("raw", None)
        item.pop("customer", None)
        item["sheet_rows"] = ref.sheet_rows
        item["carrier"] = ref.carrier
        item["order_date"] = ref.order_date.isoformat() if ref.order_date else ""
        proof_urls, first_proof_number = _display_proof_urls(ref.carrier, result.proof_urls)
        item["proof_urls"] = proof_urls
        item["proof_first_number"] = first_proof_number
        item["proof_urls_filtered"] = True
        item["bucket"] = _bucket(result)
        payload.append(item)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace(
        "<", "\\u003c"
    )


def _bucket(result: JobResult) -> str:
    if result.error:
        return "error"
    if not result.found:
        return "missing"
    if result.delivered:
        return "delivered"
    if result.status_code in {"E", "P", "401"}:
        return "rejected"
    return "pending"


def _carrier_label(carrier: str) -> str:
    return {
        "skyfrog": "KLEAN&KARE",
        "kex": "KEX",
        "interexpress": "InterExpress",
    }.get(carrier, carrier or "-")


class _atomic_text:
    def __init__(self, path: Path, *, encoding: str, newline: str) -> None:
        self.path = path
        self.encoding = encoding
        self.newline = newline
        self.file = None
        self.temp_path: Path | None = None

    def __enter__(self):
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        self.temp_path = Path(temp_name)
        self.file = os.fdopen(descriptor, "w", encoding=self.encoding, newline=self.newline)
        return self.file

    def __exit__(self, exc_type, exc, traceback):
        assert self.file is not None and self.temp_path is not None
        self.file.close()
        if exc_type is None:
            os.replace(self.temp_path, self.path)
        else:
            self.temp_path.unlink(missing_ok=True)
        return False
