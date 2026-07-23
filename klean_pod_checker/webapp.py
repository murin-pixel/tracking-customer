from __future__ import annotations

import io
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any

import requests
from flask import Flask, Response, jsonify, render_template, request, send_file, send_from_directory

from .config import Settings
from .interexpress import InterexpressClient, InterexpressError
from .kex import KEX_TRACKING_RE, KexClient, KexError
from .models import JobResult
from .proof_tokens import local_kex_proof_parts, read_sheet_proof_token
from .sheets import normalize_tracking_input
from .skyfrog import SkyfrogClient, SkyfrogError
from .supabase_mapping import SupabaseMappingError, SupabaseMappingStore
from .supabase_status_cache import SupabaseStatusCache, SupabaseStatusCacheError


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

class LiveSearchService:
    """Reuse carrier clients safely inside each web process."""

    def __init__(self, settings: Settings, status_cache: Any) -> None:
        self.settings = settings
        self.status_cache = status_cache
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
                self._cache(result)
                return result
            if carrier == "interexpress":
                result = self._interexpress_client.search_order(order_number)
                self._cache(result)
                return result
            last_error: Exception | None = None
            for _ in range(2):
                try:
                    if self._client is None:
                        self._client = self._new_client()
                    result = self._client.search_order(order_number)
                    self._cache(result)
                    return result
                except (SkyfrogError, requests.RequestException) as exc:
                    last_error = exc
                    self._discard_client()
            assert last_error is not None
            raise last_error

    def _cache(self, result: JobResult) -> None:
        try:
            self.status_cache.put(result)
        except SupabaseStatusCacheError:
            # A cache outage must not hide a successful live carrier result.
            pass


def _client_ip() -> str:
    forwarded = request.headers.get("CF-Connecting-IP", "").strip()
    return forwarded or (request.remote_addr or "unknown")


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
    is_interexpress = result.group_name.casefold() == "interexpress"
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
    if is_interexpress:
        if code == "MDE" or "คีย์ข้อมูลพัสดุเข้าระบบ" in status:
            return "received", False
        if code in {"SOP-HUB", "TKL", "SOP-DEL"} or any(
            word in status
            for word in (
                "พัสดุออกจากศูนย์คัดแยก",
                "พัสดุออกจากคลังกระจายสินค้า",
                "พัสดุออกจากศูนย์เพื่อจัดส่งให้ลูกค้า",
            )
        ):
            return "out_for_delivery", False
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
        word in status
        for word in (
            "ระหว่างขนส่ง",
            "กำลังขนส่ง",
            "ถึงศูนย์",
            "ออกจากศูนย์กระจายสินค้า",
            "ถึงคลังสินค้าปลายทาง",
            "in transit",
        )
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
        "location": result.location,
        "checked_at": result.checked_at,
        "carrier": CUSTOMER_CARRIER_LABELS.get(carrier, carrier),
    }


def _search_anb(service: Any, tracking_number: str) -> tuple[JobResult, str]:
    result = service.search(tracking_number, "kex")
    if result.found:
        return result, "kex"
    return service.search(tracking_number, "interexpress"), "interexpress"


def _search_mapped_tracking(
    service: Any, tracking_number: str, carrier: str
) -> tuple[JobResult, str]:
    if carrier == "auto":
        return _search_anb(service, tracking_number)
    result = service.search(tracking_number, carrier)
    if not result.found and carrier == "kex":
        return service.search(tracking_number, "interexpress"), "interexpress"
    return result, carrier


def _order_tracking_references(
    mapping_store: Any, order_number: str
) -> list[tuple[str, str]]:
    return mapping_store.get_tracking_refs(order_number)


def _customer_lookup(
    service: Any,
    raw_order: str,
    mapping_store: Any,
) -> tuple[JobResult, str, str]:
    """Search KLEAN first, then imported tracking, with KEX fallback."""
    candidate = re.sub(r"\s+", "", raw_order or "").lstrip("#").upper()
    try:
        carrier, order_number = normalize_tracking_input(raw_order, "auto")
    except ValueError:
        if not KEX_TRACKING_RE.fullmatch(candidate):
            raise
        result, carrier = _search_anb(service, candidate)
        return result, carrier, candidate

    if carrier == "skyfrog":
        skyfrog_error: Exception | None = None
        try:
            result = service.search(order_number, "skyfrog")
        except (SkyfrogError, requests.RequestException) as error:
            skyfrog_error = error
            result = None
        if result is not None and result.found:
            return result, "skyfrog", order_number

        references = _order_tracking_references(mapping_store, order_number)
        if references:
            mapped_result, mapped_carrier = _search_mapped_tracking(
                service, *references[0]
            )
            return mapped_result, mapped_carrier, order_number
        if result is not None:
            return result, "skyfrog", order_number
        assert skyfrog_error is not None
        raise skyfrog_error

    return service.search(order_number, carrier), carrier, order_number


def _recent_cached_result(
    cache: Any, order_number: str, max_age: timedelta
) -> JobResult | None:
    result = cache.get(order_number)
    if result is None or not result.found or result.error or not result.checked_at:
        return None
    try:
        checked_at = datetime.fromisoformat(result.checked_at)
        if checked_at.tzinfo is None:
            checked_at = checked_at.astimezone()
        if datetime.now().astimezone() - checked_at > max_age:
            return None
    except ValueError:
        return None
    return result


def _cached_carrier(result: JobResult, fallback: str) -> str:
    group = result.group_name.casefold()
    if "interexpress" in group:
        return "interexpress"
    if "kex" in group:
        return "kex"
    return "kex" if fallback == "auto" else fallback


def _cached_customer_lookup(
    raw_order: str,
    cache: Any,
    mapping_store: Any,
    *,
    max_age: timedelta = timedelta(hours=24),
) -> tuple[JobResult, str, str] | None:
    """Return a recent sanitized lookup source when a carrier is unavailable."""
    candidate = re.sub(r"\s+", "", raw_order or "").lstrip("#").upper()
    try:
        carrier, order_number = normalize_tracking_input(raw_order, "auto")
    except ValueError:
        if not KEX_TRACKING_RE.fullmatch(candidate):
            return None
        order_number, carrier = candidate, "auto"

    if carrier == "skyfrog":
        result = _recent_cached_result(cache, order_number, max_age)
        if result is not None:
            return result, "skyfrog", order_number
        references = mapping_store.get_tracking_refs(order_number)
        for tracking_number, mapped_carrier in references:
            result = _recent_cached_result(cache, tracking_number, max_age)
            if result is not None:
                return (
                    result,
                    _cached_carrier(result, mapped_carrier),
                    order_number,
                )
        return None

    result = _recent_cached_result(cache, order_number, max_age)
    if result is None:
        return None
    return result, _cached_carrier(result, carrier), order_number

def create_app(
    *,
    settings: Settings | None = None,
    search_service: Any | None = None,
    mapping_store: Any | None = None,
    status_cache: Any | None = None,
) -> Flask:
    settings = settings or Settings.from_env(require_credentials=True)
    if len(settings.web_secret_key) < 32:
        raise ValueError("กรุณาตั้งค่า WEB_SECRET_KEY อย่างน้อย 32 ตัวอักษรในไฟล์ .env")

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024
    statuses = status_cache or SupabaseStatusCache(
        settings.supabase_url,
        settings.supabase_secret_key,
        table=settings.supabase_status_table,
        timeout=min(settings.request_timeout_seconds, 15),
    )
    service = search_service or LiveSearchService(settings, statuses)
    mappings = mapping_store or SupabaseMappingStore(
        settings.supabase_url,
        settings.supabase_secret_key,
        table=settings.supabase_mapping_table,
        timeout=min(settings.request_timeout_seconds, 15),
    )
    customer_search_limiter = SlidingWindowLimiter(limit=10, seconds=60)

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        if request.endpoint not in {"static", "health", "sheet_proof"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.get("/")
    @app.get("/customer.html")
    def customer_page():
        return render_template("customer.html")

    @app.post("/api/customer-check")
    def customer_check_order():
        if not customer_search_limiter.allow(_client_ip()):
            return jsonify(error="ค้นหาถี่เกินไป กรุณารอสักครู่แล้วลองใหม่"), 429
        payload = request.get_json(silent=True) or {}
        raw_order = str(payload.get("order", ""))
        try:
            result, carrier, display_order = _customer_lookup(
                service,
                raw_order,
                mappings,
            )
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except (
            SkyfrogError,
            KexError,
            InterexpressError,
            SupabaseMappingError,
            SupabaseStatusCacheError,
            requests.RequestException,
        ):
            try:
                cached = _cached_customer_lookup(
                    raw_order,
                    statuses,
                    mappings,
                )
            except (SupabaseMappingError, SupabaseStatusCacheError):
                cached = None
            if cached is None:
                app.logger.exception("Customer carrier search failed")
                # Cloudflare can replace an origin 502 body with an HTML error page.
                # A 424 preserves our JSON response while still marking the
                # upstream carrier dependency as unavailable.
                return jsonify(error="ยังเชื่อมต่อระบบขนส่งไม่ได้ กรุณาลองใหม่อีกครั้ง"), 424
            app.logger.warning("Carrier unavailable; serving recent cached customer status")
            result, carrier, display_order = cached
            customer_result = _customer_result(result, carrier)
            customer_result["lookup_order"] = display_order
            customer_result["cached"] = True
            return jsonify(result=customer_result)
        customer_result = _customer_result(result, carrier)
        customer_result["lookup_order"] = display_order
        return jsonify(result=customer_result)

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

    return app
