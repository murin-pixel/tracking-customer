from __future__ import annotations

import csv
import io
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import requests
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)

from .config import Settings
from .interexpress import InterexpressClient, InterexpressError
from .kex import KEX_TRACKING_RE, KexClient, KexError
from .models import JobResult
from .multiple_tracking_sync import import_multiple_tracking_sheet
from .proof_tokens import local_kex_proof_parts, read_sheet_proof_token
from .shopee import SHOPEE_ORDER_RE, extract_shopee_tracking
from .sheets import KLEAN_ORDER_RE, normalize_auto_search_input, normalize_tracking_input
from .sheets_sync import GoogleSheetsWriter
from .skyfrog import SkyfrogClient, SkyfrogError
from .storage import StatusCache


class SlidingWindowLimiter:
    def __init__(self, *, limit: int, seconds: int) -> None:
        self.limit = limit
        self.seconds = seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True

    def clear(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


class LiveSearchService:
    """Reuse carrier clients safely inside each web process."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: SkyfrogClient | None = None
        self._kex_client = KexClient(
            settings.kex_proof_pin,
            settings.kex_proof_dir,
            timeout=settings.request_timeout_seconds,
        )
        self._interexpress_client = InterexpressClient(
            settings.interexpress_username,
            settings.interexpress_password,
            timeout=settings.request_timeout_seconds,
        )
        self._lock = threading.Lock()

    def _new_client(self) -> SkyfrogClient:
        client = SkyfrogClient(
            self.settings.skyfrog_customer_code,
            self.settings.skyfrog_username,
            self.settings.skyfrog_password,
            timeout=self.settings.request_timeout_seconds,
            request_delay=self.settings.request_delay_seconds,
        )
        client.login()
        return client

    def _discard_client(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None

    def search(self, order_number: str, carrier: str = "skyfrog") -> JobResult:
        with self._lock:
            if carrier == "kex":
                result = self._kex_client.search_order(order_number)
                cache = StatusCache(self.settings.state_db_path)
                try:
                    cache.put(result)
                finally:
                    cache.close()
                return result
            if carrier == "interexpress":
                result = self._interexpress_client.search_order(order_number)
                cache = StatusCache(self.settings.state_db_path)
                try:
                    cache.put(result)
                finally:
                    cache.close()
                return result
            last_error: Exception | None = None
            for _ in range(2):
                try:
                    if self._client is None:
                        self._client = self._new_client()
                    result = self._client.search_order(order_number)
                    cache = StatusCache(self.settings.state_db_path)
                    try:
                        cache.put(result)
                    finally:
                        cache.close()
                    return result
                except (SkyfrogError, requests.RequestException) as exc:
                    last_error = exc
                    self._discard_client()
            assert last_error is not None
            raise last_error


def _client_ip() -> str:
    forwarded = request.headers.get("CF-Connecting-IP", "").strip()
    return forwarded or (request.remote_addr or "unknown")


def _is_authenticated() -> bool:
    return session.get("cs_authenticated") is True


def _safe_next_url(value: str) -> str:
    return value if value.startswith("/") and not value.startswith("//") else "/"


def _login_required(api: bool = False) -> Callable:
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any):
            if not _is_authenticated():
                if api:
                    return jsonify(error="กรุณาเข้าสู่ระบบใหม่"), 401
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def _report_summary(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "total": 0,
        "delivered": 0,
        "pending": 0,
        "missing": 0,
        "updated": "ยังไม่มีรายงาน",
    }
    if not path.exists():
        return summary
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                summary["total"] += 1
                if row.get("delivered", "").casefold() == "true":
                    summary["delivered"] += 1
                elif row.get("found", "").casefold() != "true":
                    summary["missing"] += 1
                else:
                    summary["pending"] += 1
        modified = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        summary["updated"] = modified.strftime("%d/%m/%Y %H:%M น.")
    except (OSError, csv.Error):
        summary["updated"] = "อ่านรายงานไม่ได้"
    return summary


def _public_result(result: JobResult, carrier: str = "") -> dict[str, Any]:
    payload = asdict(result)
    payload.pop("raw", None)
    payload.pop("customer", None)
    if carrier == "skyfrog":
        # Skyfrog places two customer-signature captures before the actual
        # delivery photos. They are sensitive and must not appear in live CS
        # search results.
        payload["proof_urls"] = list(payload.get("proof_urls") or [])[2:]
        payload["proof_urls_filtered"] = True
    return payload


def _search_with_carrier_priority(
    service: Any, order_number: str
) -> tuple[JobResult, str]:
    """Try every carrier in the CS automatic-search priority order."""
    last_result: JobResult | None = None
    last_carrier = "interexpress"
    last_error: Exception | None = None
    for carrier in ("skyfrog", "kex", "interexpress"):
        try:
            result = service.search(order_number, carrier)
        except (SkyfrogError, KexError, InterexpressError, requests.RequestException) as error:
            # A temporary carrier outage must not prevent the next carrier
            # from being checked for an ambiguous tracking number.
            last_error = error
            continue
        if result.found:
            return result, carrier
        last_result = result
        last_carrier = carrier
    if last_result is not None:
        return last_result, last_carrier
    assert last_error is not None
    raise last_error


def _tracking_group_for_input(
    state_db_path: Path, raw_value: str
) -> tuple[str, list[tuple[str, str]]] | None:
    """Resolve an order or a tracking number to its imported Shopee group."""
    candidate = re.sub(r"\s+", "", raw_value or "").lstrip("#").upper()
    if not candidate:
        return None
    cache = StatusCache(state_db_path)
    try:
        if SHOPEE_ORDER_RE.fullmatch(candidate):
            references = cache.get_shopee_tracking_refs(candidate)
            if not references:
                references = _mapping_references_for_order(cache, candidate)
            return (candidate, references) if references else None
        tracking_match = KEX_TRACKING_RE.search(candidate)
        if not tracking_match:
            return None
        tracking_number = tracking_match.group(0).upper()
        order_number = cache.get_shopee_order_for_tracking(tracking_number)
        if not order_number:
            order_number = cache.get_shopee_mapping_order_for_tracking(tracking_number)
        if not order_number:
            return None
        references = cache.get_shopee_tracking_refs(order_number)
        if not references:
            references = _mapping_references_for_order(cache, order_number)
        return (order_number, references) if references else None
    finally:
        cache.close()


def _mapping_references_for_order(
    cache: StatusCache, order_number: str
) -> list[tuple[str, str]]:
    """Resolve raw Mapping Order values into carrier-ready tracking references."""
    references: dict[tuple[str, str], None] = {}
    for raw_value in cache.get_shopee_mapping_values(order_number):
        klean_match = KLEAN_ORDER_RE.search(raw_value)
        if klean_match:
            references[(klean_match.group(1).upper(), "skyfrog")] = None
            continue
        extracted = extract_shopee_tracking(raw_value)
        if extracted:
            carrier, tracking_number = extracted
            references[(tracking_number, carrier)] = None
            continue
        anb_match = KEX_TRACKING_RE.search(raw_value)
        if anb_match:
            references[(anb_match.group(0).upper(), "auto")] = None
    return sorted(references, key=lambda item: item[0])


def _search_group_tracking(
    service: Any, tracking_number: str, carrier: str
) -> tuple[JobResult, str]:
    """Check an imported ANB reference without querying Skyfrog unnecessarily."""
    if carrier == "skyfrog":
        carriers = ("skyfrog",)
    elif carrier == "interexpress":
        carriers = ("interexpress",)
    else:
        # The grouped-tracking tab does not include the carrier.  Try KEX first,
        # then InterExpress; a KEX value that is no longer present can therefore
        # still be resolved as InterExpress.
        carriers = ("kex", "interexpress")

    last_result: JobResult | None = None
    last_carrier = carriers[-1]
    last_error: Exception | None = None
    for selected_carrier in carriers:
        try:
            result = service.search(tracking_number, selected_carrier)
        except (
            SkyfrogError,
            KexError,
            InterexpressError,
            requests.RequestException,
        ) as error:
            last_error = error
            continue
        last_result = result
        last_carrier = selected_carrier
        if result.found:
            return result, selected_carrier
    if last_result is not None:
        return last_result, last_carrier
    return (
        JobResult(
            order_number=tracking_number,
            found=False,
            checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            error="carrier_unavailable" if last_error else "tracking_unavailable",
        ),
        last_carrier,
    )


def _group_summary(order_number: str, entries: list[tuple[str, str, JobResult]]) -> dict[str, Any]:
    total = len(entries)
    delivered = sum(result.delivered for _, _, result in entries)
    errors = sum(bool(result.error) for _, _, result in entries)
    missing = sum(not result.found and not result.error for _, _, result in entries)
    return {
        "order_number": order_number,
        "total": total,
        "delivered": delivered,
        "pending": total - delivered - errors - missing,
        "missing": missing,
        "errors": errors,
        "multiple": total > 1,
    }


def _public_group_entry(
    tracking_number: str, carrier: str, result: JobResult
) -> dict[str, Any]:
    return {
        "tracking_number": tracking_number,
        "carrier": CUSTOMER_CARRIER_LABELS.get(carrier, carrier),
        "result": _public_result(result, carrier),
    }


def _sync_checked_results(
    writer: Any,
    report_path: Path,
    entries: list[tuple[str, str, JobResult]],
) -> dict[str, Any]:
    sync = {"enabled": bool(writer.enabled), "updated_rows": 0, "ok": True}
    if not writer.enabled:
        return sync
    try:
        for tracking_number, carrier, result in entries:
            rows = _report_sheet_rows(report_path, tracking_number)
            sync["updated_rows"] += writer.update_rows(rows, result, carrier=carrier)
    except Exception:
        sync["ok"] = False
    return sync


CUSTOMER_CARRIER_LABELS = {
    "skyfrog": "KLEAN&KARE",
    "kex": "KEX",
    "interexpress": "InterExpress",
}
CUSTOMER_EXCEPTION_CODES = {"E", "P", "060.01M", "060.09", "060.10", "112"}


def _customer_stage(result: JobResult) -> tuple[str, bool]:
    """Map carrier-specific statuses to the four public tracking stages."""
    code = result.status_code.strip().upper()
    status = f"{result.status_th} {result.status_en}".casefold()
    exception = code in CUSTOMER_EXCEPTION_CODES or any(
        word in status
        for word in (
            "จัดส่งไม่สำเร็จ",
            "ปฏิเสธรับ",
            "ส่งคืน",
            "ตีกลับ",
            "failed delivery",
            "return to sender",
        )
    )
    if result.delivered or code in {"A", "C", "POD", "PODEX", "400", "401"}:
        return "delivered", False
    if exception:
        return "in_transit", True
    if code in {"S", "045", "300"} or any(
        word in status
        for word in (
            "ออกนำส่ง",
            "กำลังนำส่ง",
            "กำลังจัดส่งพัสดุ",
            "ระหว่างการนำส่ง",
            "นำจ่าย",
            "out for delivery",
        )
    ):
        return "out_for_delivery", False
    if code in {"109", "200", "SIP-LH"} or any(
        word in status for word in ("ระหว่างขนส่ง", "กำลังขนส่ง", "ถึงศูนย์", "in transit")
    ):
        return "in_transit", False
    return "received", False


def _customer_result(result: JobResult, carrier: str) -> dict[str, Any]:
    """Return only delivery information appropriate for a public lookup."""
    stage, exception = _customer_stage(result)
    return {
        "order_number": result.order_number,
        "found": result.found,
        "status_code": result.status_code,
        "status_th": result.status_th,
        "status_en": result.status_en,
        "delivered": result.delivered,
        "stage": stage,
        "exception": exception,
        "created_at": result.created_at,
        "delivery_at": result.delivery_at,
        "updated_at": result.updated_at,
        "checked_at": result.checked_at,
        "carrier": CUSTOMER_CARRIER_LABELS.get(carrier, carrier),
    }


def _customer_lookup(
    service: Any,
    raw_order: str,
    state_db_path: Path,
) -> tuple[JobResult, str, str]:
    """Search with customer-friendly carrier detection and KEX fallback."""
    candidate = re.sub(r"\s+", "", raw_order or "").lstrip("#").upper()
    if SHOPEE_ORDER_RE.fullmatch(candidate):
        cache = StatusCache(state_db_path)
        try:
            tracking_refs = cache.get_shopee_tracking_refs(candidate)
        finally:
            cache.close()
        if tracking_refs:
            tracking_number, carrier = tracking_refs[0]
            result = service.search(tracking_number, carrier)
            if not result.found and carrier == "kex":
                result = service.search(tracking_number, "interexpress")
                carrier = "interexpress"
            return result, carrier, candidate
    try:
        carrier, order_number = normalize_tracking_input(raw_order, "auto")
    except ValueError:
        if not KEX_TRACKING_RE.fullmatch(candidate):
            raise
        result = service.search(candidate, "kex")
        if result.found:
            return result, "kex", candidate
        return service.search(candidate, "interexpress"), "interexpress", candidate
    return service.search(order_number, carrier), carrier, order_number


def _report_sheet_rows(path: Path, order_number: str) -> list[int]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("order_number", "").upper() != order_number.upper():
                    continue
                values = row.get("sheet_rows", "").split(",")
                return [int(value.strip()) for value in values if value.strip().isdigit()]
    except (OSError, csv.Error):
        return []
    return []


def create_app(
    *,
    settings: Settings | None = None,
    search_service: Any | None = None,
    sheet_writer: Any | None = None,
    multiple_tracking_sync: Callable[[], int] | None = None,
) -> Flask:
    settings = settings or Settings.from_env(require_credentials=True)
    if not settings.cs_access_pin:
        raise ValueError("กรุณาตั้งค่า CS_ACCESS_PIN ในไฟล์ .env")
    if len(settings.web_secret_key) < 32:
        raise ValueError("กรุณาตั้งค่า WEB_SECRET_KEY อย่างน้อย 32 ตัวอักษรในไฟล์ .env")

    app = Flask(__name__)
    app.secret_key = settings.web_secret_key
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=settings.web_session_hours),
        MAX_CONTENT_LENGTH=8 * 1024,
    )
    service = search_service or LiveSearchService(settings)
    writer = sheet_writer or GoogleSheetsWriter(settings)
    sync_multiple_tracking = multiple_tracking_sync or (
        lambda: import_multiple_tracking_sheet(
            settings.sheet_id,
            settings.state_db_path,
            timeout=settings.request_timeout_seconds,
        )
    )
    login_limiter = SlidingWindowLimiter(limit=8, seconds=10 * 60)
    search_limiter = SlidingWindowLimiter(limit=30, seconds=60)
    customer_search_limiter = SlidingWindowLimiter(limit=10, seconds=60)

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://www.skyfrog.net https://skyfrog.net; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.endpoint not in {"static", "health", "sheet_proof"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET" and _is_authenticated():
            return redirect(url_for("dashboard"))
        error = ""
        next_url = _safe_next_url(request.values.get("next", "/"))
        if request.method == "POST":
            ip = _client_ip()
            if not login_limiter.allow(ip):
                return render_template(
                    "login.html",
                    error="ลอง PIN หลายครั้งเกินไป กรุณารอ 10 นาที",
                    next_url=next_url,
                ), 429
            pin = request.form.get("pin", "")
            if secrets.compare_digest(pin, settings.cs_access_pin):
                login_limiter.clear(ip)
                session.clear()
                session.permanent = True
                session["cs_authenticated"] = True
                return redirect(next_url)
            error = "PIN ไม่ถูกต้อง กรุณาลองอีกครั้ง"
        return render_template("login.html", error=error, next_url=next_url)

    @app.post("/logout")
    @_login_required()
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @_login_required()
    def dashboard():
        return render_template(
            "dashboard.html",
            summary=_report_summary(settings.output_dir / "latest.csv"),
        )

    @app.get("/customer.html")
    def customer_page():
        return render_template("customer.html")

    @app.post("/api/customer-check")
    def customer_check_order():
        if not customer_search_limiter.allow(_client_ip()):
            return jsonify(error="ค้นหาถี่เกินไป กรุณารอสักครู่แล้วลองใหม่"), 429
        payload = request.get_json(silent=True) or {}
        try:
            result, carrier, display_order = _customer_lookup(
                service,
                str(payload.get("order", "")),
                settings.state_db_path,
            )
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except (
            SkyfrogError,
            KexError,
            InterexpressError,
            requests.RequestException,
        ):
            app.logger.exception("Customer carrier search failed")
            return jsonify(error="ยังเชื่อมต่อระบบขนส่งไม่ได้ กรุณาลองใหม่อีกครั้ง"), 502
        customer_result = _customer_result(result, carrier)
        customer_result["lookup_order"] = display_order
        return jsonify(result=customer_result)

    @app.post("/api/check")
    @_login_required(api=True)
    def check_order():
        if not search_limiter.allow(_client_ip()):
            return jsonify(error="ค้นหาถี่เกินไป กรุณารอสักครู่แล้วลองใหม่"), 429
        payload = request.get_json(silent=True) or {}
        raw_order = str(payload.get("order", ""))
        searched_candidate = re.sub(r"\s+", "", raw_order).lstrip("#").upper()
        searched_with_tracking = bool(KEX_TRACKING_RE.fullmatch(searched_candidate))
        try:
            # Refresh the grouped-tracking tab before every CS search so a
            # newly added split shipment is available immediately.
            sync_multiple_tracking()
        except (OSError, ValueError, requests.RequestException):
            # Keep the last successfully imported mapping available if Google
            # Sheets is temporarily unavailable; live carrier checking still
            # proceeds normally.
            app.logger.warning("Grouped tracking sheet refresh failed", exc_info=True)
        group = _tracking_group_for_input(settings.state_db_path, raw_order)
        display_order_number: str | None = None
        try:
            if group:
                group_order, tracking_refs = group
                entries: list[tuple[str, str, JobResult]] = []
                for tracking_number, stored_carrier in tracking_refs:
                    result, resolved_carrier = _search_group_tracking(
                        service, tracking_number, stored_carrier
                    )
                    entries.append((tracking_number, resolved_carrier, result))
                sync = _sync_checked_results(
                    writer, settings.output_dir / "latest.csv", entries
                )
                if not sync["ok"]:
                    app.logger.error("Google Sheet sync failed for grouped tracking %s", group_order)
                group_payload = _group_summary(group_order, entries)
                if group_payload["multiple"]:
                    return jsonify(
                        result=_public_result(entries[0][2], entries[0][1]),
                        results=[
                            _public_group_entry(tracking, carrier, result)
                            for tracking, carrier, result in entries
                        ],
                        group=group_payload,
                        sheet_sync=sync,
                    )
                order_number, carrier, result = entries[0]
                display_order_number = group_order
            else:
                requested_carrier = str(payload.get("carrier", "auto")).strip().lower()
                if requested_carrier == "auto":
                    order_number = normalize_auto_search_input(raw_order)
                    result, carrier = _search_with_carrier_priority(service, order_number)
                else:
                    carrier, order_number = normalize_tracking_input(raw_order, requested_carrier)
                    result = service.search(order_number, carrier)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except (
            SkyfrogError,
            KexError,
            InterexpressError,
            requests.RequestException,
        ):
            app.logger.exception("Carrier search failed for %s", order_number)
            return jsonify(error="เชื่อมต่อระบบขนส่งไม่สำเร็จ กรุณาลองใหม่อีกครั้ง"), 502
        sync = _sync_checked_results(
            writer,
            settings.output_dir / "latest.csv",
            [(order_number, carrier, result)],
        )
        if not sync["ok"]:
            app.logger.error("Google Sheet sync failed for %s", order_number)
        result_payload = _public_result(result, carrier)
        result_payload["carrier"] = CUSTOMER_CARRIER_LABELS.get(carrier, carrier)
        if display_order_number:
            result_payload["tracking_number"] = order_number
            result_payload["mapping_order_number"] = display_order_number
            if searched_with_tracking:
                result_payload["order_number"] = display_order_number
        return jsonify(result=result_payload, sheet_sync=sync)

    @app.get("/report")
    @app.get("/latest.html")
    @_login_required()
    def report():
        path = settings.output_dir / "latest.html"
        if not path.exists():
            return "ยังไม่มีรายงาน กรุณารอรอบตรวจอัตโนมัติ", 404
        return send_file(path, mimetype="text/html", conditional=False)

    @app.get("/proof/kex/<tracking>/<filename>")
    @_login_required()
    def kex_proof(tracking: str, filename: str):
        tracking = tracking.upper()
        if not KEX_TRACKING_RE.fullmatch(tracking):
            return "ไม่พบรูปหลักฐาน", 404
        if not re.fullmatch(r"proof-\d+-[0-9a-f]{10}\.(?:jpg|png|webp)", filename):
            return "ไม่พบรูปหลักฐาน", 404
        return send_from_directory(
            settings.kex_proof_dir / tracking,
            filename,
            conditional=True,
        )

    @app.get("/sheet-proof/<token>")
    def sheet_proof(token: str):
        try:
            source = read_sheet_proof_token(token, settings.web_secret_key)
        except ValueError:
            return "ไม่พบรูปหลักฐาน", 404
        local_proof = local_kex_proof_parts(source)
        if local_proof:
            tracking, filename = local_proof
            return send_from_directory(
                settings.kex_proof_dir / tracking,
                filename,
                conditional=True,
                max_age=3600,
            )
        try:
            upstream = requests.get(
                source,
                timeout=settings.request_timeout_seconds,
                headers={"User-Agent": "Google-Sheets-Proof-Proxy/1.0"},
            )
            upstream.raise_for_status()
        except requests.RequestException:
            return "ไม่สามารถโหลดรูปหลักฐาน", 502
        content_type = upstream.headers.get("Content-Type", "").split(";", 1)[0]
        if not content_type.startswith("image/") or len(upstream.content) > 10 * 1024 * 1024:
            return "ไม่พบรูปหลักฐาน", 404
        return send_file(
            io.BytesIO(upstream.content),
            mimetype=content_type,
            conditional=True,
            max_age=3600,
        )

    @app.get("/download/latest.csv")
    @_login_required()
    def download_report():
        path = settings.output_dir / "latest.csv"
        if not path.exists():
            return "ยังไม่มีรายงาน กรุณารอรอบตรวจอัตโนมัติ", 404
        return send_file(
            path,
            mimetype="text/csv",
            as_attachment=True,
            download_name="klean-kare-latest.csv",
            conditional=False,
        )

    return app
