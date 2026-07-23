from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class OrderRef:
    order_number: str
    order_date: date | None
    carrier: str = "skyfrog"
    sheet_rows: list[int] = field(default_factory=list)
    source_values: list[str] = field(default_factory=list)

    def merge(self, row_number: int, source_value: str) -> None:
        if row_number not in self.sheet_rows:
            self.sheet_rows.append(row_number)
        clean = source_value.strip()
        if clean and clean not in self.source_values:
            self.source_values.append(clean)


@dataclass
class JobResult:
    order_number: str
    found: bool
    status_code: str = ""
    status_en: str = ""
    status_th: str = ""
    delivered: bool = False
    driver: str = ""
    group_name: str = ""
    created_at: str = ""
    delivery_at: str = ""
    updated_at: str = ""
    location: str = ""
    customer: str = ""
    proof_urls: list[str] = field(default_factory=list)
    checked_at: str = ""
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def final(self) -> bool:
        return self.status_code.upper() in {"A", "C", "POD", "400", "401"}
