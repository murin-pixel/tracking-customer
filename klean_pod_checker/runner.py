from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .config import Settings
from .interexpress import InterexpressClient
from .kex import KexClient
from .models import JobResult, OrderRef
from .report import write_reports
from .sheets import extract_order_refs, fetch_sheet_csv, normalize_tracking_input, parse_order_date
from .sheets_sync import GoogleSheetsWriter
from .skyfrog import SkyfrogClient
from .storage import StatusCache


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ตรวจสถานะจัดส่งจาก Google Sheet ผ่าน Skyfrog และ KEX"
    )
    parser.add_argument(
        "--sheet-csv",
        type=Path,
        help="อ่าน CSV ในเครื่องแทนการดาวน์โหลด Google Sheet",
    )
    parser.add_argument(
        "--order",
        action="append",
        default=[],
        help="ตรวจเฉพาะเลขออเดอร์ (ระบุซ้ำได้ และไม่อ่านชีต)",
    )
    parser.add_argument("--limit", type=int, help="จำกัดจำนวนออเดอร์สำหรับทดสอบ")
    parser.add_argument(
        "--skip-final",
        action="store_true",
        help="ใช้ผล cache สำหรับออเดอร์ที่ Completed แล้ว",
    )
    parser.add_argument("--concurrency", type=int, help="จำนวนคำขอพร้อมกัน")
    parser.add_argument(
        "--no-history", action="store_true", help="ไม่สร้างไฟล์ประวัติ timestamp"
    )
    return parser


def run(args: argparse.Namespace) -> dict[str, Path]:
    settings = Settings.from_env(require_credentials=True)
    refs = _load_refs(args, settings)
    if args.limit is not None:
        refs = refs[: max(0, args.limit)]
    if not refs:
        raise RuntimeError("ไม่พบเลขออเดอร์ KLEAN&KARE หรือพัสดุ KEX ในข้อมูลต้นทาง")

    print(f"พบรายการติดตามไม่ซ้ำ {len(refs)} รายการ", flush=True)
    cache = StatusCache(settings.state_db_path)
    master: SkyfrogClient | None = None
    try:
        results: dict[str, JobResult] = {}
        pending: list[OrderRef] = []
        for ref in refs:
            cached = cache.get_final(ref.order_number) if args.skip_final else None
            if cached is not None:
                results[ref.order_number] = cached
            else:
                pending.append(ref)

        if results:
            print(f"ใช้ cache สำหรับออเดอร์ Completed {len(results)} รายการ", flush=True)
        skyfrog_refs = [ref for ref in pending if ref.carrier == "skyfrog"]
        kex_refs = [ref for ref in pending if ref.carrier == "kex"]
        interexpress_refs = [ref for ref in pending if ref.carrier == "interexpress"]
        if skyfrog_refs:
            master = SkyfrogClient(
                settings.skyfrog_customer_code,
                settings.skyfrog_username,
                settings.skyfrog_password,
                timeout=settings.request_timeout_seconds,
                request_delay=settings.request_delay_seconds,
            )
            print("กำลังเข้าสู่ระบบ Skyfrog...", flush=True)
            try:
                master.login()
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                print(
                    f"WARNING: Skyfrog เข้าไม่ได้ชั่วคราว; ข้าม {len(skyfrog_refs)} รายการ",
                    file=sys.stderr,
                    flush=True,
                )
                for ref in skyfrog_refs:
                    cached = cache.get(ref.order_number)
                    result = (
                        cached
                        if cached is not None and not cached.error
                        else JobResult(
                            order_number=ref.order_number,
                            found=False,
                            checked_at=datetime.now()
                            .astimezone()
                            .isoformat(timespec="seconds"),
                            error=message,
                        )
                    )
                    results[ref.order_number] = result
            else:
                print("เข้าสู่ระบบสำเร็จ", flush=True)
                _query_skyfrog_orders(
                    skyfrog_refs,
                    results,
                    master,
                    cache,
                    concurrency=args.concurrency or settings.concurrency,
                )
        if kex_refs:
            _query_kex_orders(
                kex_refs,
                results,
                settings,
                cache,
                concurrency=min(args.concurrency or settings.concurrency, 2),
            )
        if interexpress_refs:
            _query_interexpress_orders(
                interexpress_refs,
                results,
                settings,
                cache,
                concurrency=min(args.concurrency or settings.concurrency, 2),
            )

        report_rows = [(ref, results[ref.order_number]) for ref in refs]
        paths = write_reports(
            report_rows,
            settings.output_dir,
            keep_history=not args.no_history,
        )
        writer = GoogleSheetsWriter(settings)
        try:
            if writer.enabled:
                try:
                    updated = writer.update_report_rows(report_rows)
                    print(f"อัปเดต Google Sheet คอลัมน์สถานะแล้ว {updated} แถว", flush=True)
                except Exception as exc:
                    print(
                        f"WARNING: เขียน Google Sheet ไม่สำเร็จ: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
        finally:
            writer.close()
        _print_summary(report_rows, paths)
        return paths
    finally:
        if master is not None:
            master.close()
        cache.close()


def _load_refs(args: argparse.Namespace, settings: Settings) -> list[OrderRef]:
    if args.order:
        refs = []
        seen = set()
        for raw in args.order:
            carrier, order = normalize_tracking_input(raw)
            if order and order not in seen:
                seen.add(order)
                refs.append(
                    OrderRef(
                        order_number=order,
                        order_date=parse_order_date(order) if carrier == "skyfrog" else None,
                        carrier=carrier,
                    )
                )
        return refs

    if args.sheet_csv:
        csv_text = args.sheet_csv.read_text(encoding="utf-8-sig")
    else:
        print("กำลังดาวน์โหลดข้อมูลจาก Google Sheet...", flush=True)
        csv_text = fetch_sheet_csv(
            settings.sheet_csv_url,
            timeout=settings.request_timeout_seconds,
        )
    return extract_order_refs(csv_text, tracking_column=settings.sheet_tracking_column)


def _query_skyfrog_orders(
    refs: list[OrderRef],
    results: dict[str, JobResult],
    master: SkyfrogClient,
    cache: StatusCache,
    *,
    concurrency: int,
) -> None:
    if not refs:
        return
    local = threading.local()
    cache_lock = threading.Lock()

    def search(ref: OrderRef) -> JobResult:
        client = getattr(local, "client", None)
        if client is None:
            client = master.fork()
            local.client = client
        try:
            return client.search_order(ref.order_number)
        except Exception as exc:  # keep the remaining orders running
            return JobResult(
                order_number=ref.order_number,
                found=False,
                checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                error=f"{type(exc).__name__}: {exc}",
            )

    completed = 0
    total = len(refs)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = {executor.submit(search, ref): ref for ref in refs}
        for future in as_completed(futures):
            ref = futures[future]
            result = future.result()
            results[ref.order_number] = result
            with cache_lock:
                if not result.error:
                    cache.put(result)
            completed += 1
            if completed == 1 or completed % 10 == 0 or completed == total:
                print(f"ตรวจแล้ว {completed}/{total}", flush=True)


def _query_kex_orders(
    refs: list[OrderRef],
    results: dict[str, JobResult],
    settings: Settings,
    cache: StatusCache,
    *,
    concurrency: int,
) -> None:
    if not refs:
        return
    local = threading.local()
    cache_lock = threading.Lock()

    def search(ref: OrderRef) -> JobResult:
        client = getattr(local, "kex_client", None)
        if client is None:
            client = KexClient(
                settings.kex_proof_pin,
                settings.kex_proof_dir,
                timeout=settings.request_timeout_seconds,
            )
            local.kex_client = client
        try:
            return client.search_order(ref.order_number)
        except Exception as exc:
            return JobResult(
                order_number=ref.order_number,
                found=False,
                checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                error=f"{type(exc).__name__}: {exc}",
            )

    completed = 0
    total = len(refs)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = {executor.submit(search, ref): ref for ref in refs}
        for future in as_completed(futures):
            ref = futures[future]
            result = future.result()
            results[ref.order_number] = result
            with cache_lock:
                if not result.error:
                    cache.put(result)
            completed += 1
            if completed == 1 or completed % 10 == 0 or completed == total:
                print(f"ตรวจ KEX แล้ว {completed}/{total}", flush=True)


def _query_interexpress_orders(
    refs: list[OrderRef],
    results: dict[str, JobResult],
    settings: Settings,
    cache: StatusCache,
    *,
    concurrency: int,
) -> None:
    if not refs:
        return
    local = threading.local()
    cache_lock = threading.Lock()

    def search(ref: OrderRef) -> JobResult:
        client = getattr(local, "interexpress_client", None)
        if client is None:
            client = InterexpressClient(
                settings.interexpress_username,
                settings.interexpress_password,
                timeout=settings.request_timeout_seconds,
            )
            local.interexpress_client = client
        try:
            return client.search_order(ref.order_number)
        except Exception as exc:
            return JobResult(
                order_number=ref.order_number,
                found=False,
                checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                error=f"{type(exc).__name__}: {exc}",
            )

    completed = 0
    total = len(refs)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = {executor.submit(search, ref): ref for ref in refs}
        for future in as_completed(futures):
            ref = futures[future]
            result = future.result()
            results[ref.order_number] = result
            with cache_lock:
                if not result.error:
                    cache.put(result)
            completed += 1
            if completed == 1 or completed % 10 == 0 or completed == total:
                print(f"ตรวจ InterExpress แล้ว {completed}/{total}", flush=True)


def _print_summary(rows, paths: dict[str, Path]) -> None:
    delivered = sum(result.delivered for _, result in rows)
    missing = sum(not result.found and not result.error for _, result in rows)
    errors = sum(bool(result.error) for _, result in rows)
    print(
        f"เสร็จแล้ว: จัดส่งสำเร็จ {delivered}, ไม่พบ {missing}, ผิดพลาด {errors}",
        flush=True,
    )
    print(f"CSV: {paths['latest_csv']}", flush=True)
    print(f"HTML: {paths['latest_html']}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run(args)
        return 0
    except KeyboardInterrupt:
        print("ยกเลิกโดยผู้ใช้", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
