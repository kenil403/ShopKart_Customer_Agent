"""Loads orders.json and normalises it into one predictable shape.

The assessment's orders.json schema is not fixed here, so every field is read
through a list of aliases. If the official file uses different names, add the
alias below; nothing else in the codebase needs to change.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ALIASES = {
    "order_id": ["order_id", "id", "orderId", "order_number"],
    "email": ["customer_email", "email", "customerEmail", "user_email"],
    "name": ["customer_name", "name", "customerName"],
    "status": ["status", "order_status", "state"],
    "order_date": ["order_date", "ordered_on", "placed_on", "created_at", "orderDate"],
    "delivered_date": ["delivered_date", "delivery_date", "delivered_on", "deliveredAt", "deliveredDate"],
    "items": ["items", "products", "line_items"],
    "total": ["total", "order_total", "amount", "total_amount"],
}


def _pick(raw: dict, key: str, default: Any = None) -> Any:
    for alias in ALIASES[key]:
        if alias in raw and raw[alias] not in (None, ""):
            return raw[alias]
    return default


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


@dataclass
class Item:
    name: str
    category: str
    price: float | None = None
    quantity: int = 1
    final_sale: bool = False
    customised: bool = False
    opened: bool | None = None


@dataclass
class Order:
    order_id: str
    email: str
    name: str
    status: str
    order_date: date | None
    delivered_date: date | None
    items: list[Item]
    total: float | None
    extra: dict = field(default_factory=dict)

    def public_view(self) -> dict:
        """What is safe to show a *verified* customer."""
        view = {
            "order_id": self.order_id,
            "status": self.status,
            "order_date": self.order_date.isoformat() if self.order_date else None,
            "delivered_date": self.delivered_date.isoformat() if self.delivered_date else None,
            "items": [
                {"name": i.name, "category": i.category, "price": i.price, "quantity": i.quantity,
                 **({"final_sale": True} if i.final_sale else {})}
                for i in self.items
            ],
            "total": self.total,
        }
        for k in ("expected_delivery", "payment_method", "city", "tracking_id"):
            if k in self.extra:
                view[k] = self.extra[k]
        return view


def _parse_items(raw: dict) -> list[Item]:
    raw_items = _pick(raw, "items", None)
    if raw_items is None:  # single-product orders stored flat
        raw_items = [{"name": raw.get("product") or raw.get("item") or "Item",
                      "category": raw.get("category", "unknown"),
                      "price": raw.get("price"),
                      "final_sale": raw.get("final_sale", False)}]
    items = []
    for it in raw_items:
        if isinstance(it, str):
            it = {"name": it}
        items.append(Item(
            name=it.get("name") or it.get("product") or it.get("title") or "Item",
            category=str(it.get("category") or raw.get("category") or "unknown").lower(),
            price=it.get("price"),
            quantity=int(it.get("quantity", it.get("qty", 1)) or 1),
            final_sale=bool(it.get("final_sale") or it.get("is_final_sale") or raw.get("final_sale")),
            customised=bool(it.get("customised") or it.get("customized") or it.get("personalised")),
            opened=it.get("opened"),
        ))
    return items


class OrdersRepo:
    def __init__(self, path: Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = data["orders"] if isinstance(data, dict) and "orders" in data else data
        known = {a for aliases in ALIASES.values() for a in aliases}
        self.orders: dict[str, Order] = {}
        for raw in rows:
            order = Order(
                order_id=str(_pick(raw, "order_id")).strip(),
                email=str(_pick(raw, "email", "")).strip().lower(),
                name=_pick(raw, "name", ""),
                status=str(_pick(raw, "status", "unknown")).lower(),
                order_date=_to_date(_pick(raw, "order_date")),
                delivered_date=_to_date(_pick(raw, "delivered_date")),
                items=_parse_items(raw),
                total=_pick(raw, "total"),
                extra={k: v for k, v in raw.items() if k not in known},
            )
            self.orders[order.order_id.upper()] = order

    def get(self, order_id: str) -> Order | None:
        return self.orders.get(str(order_id).strip().upper())

    def by_email(self, email: str) -> list[Order]:
        email = email.strip().lower()
        return [o for o in self.orders.values() if o.email == email]
