"""Return eligibility under the CURRENT policy.

Why this is code and not left to the LLM: a return is an irreversible action on
a real order. The LLM explains the result, but the yes/no decision is made here,
deterministically, and every decision carries the policy section it came from.

Policy precedence (hardest decision in the project):
  * 03_returns_policy_update_2026.md (effective 1 Mar 2026) REPLACES the return
    window and pickup-charge rules of 02_returns_policy_2024.md.
  * Every other 2024 rule (non-returnable items, condition of item, exchanges)
    still applies because the update says "all other rules remain unchanged".
  * The 2026 update only defines windows for clothing, footwear, home & kitchen,
    and electronics & accessories. The old 7-day "most items" window was
    replaced, so for any OTHER category we do not invent a window: the request
    is sent to manual review instead of guessing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.orders_repo import Item, Order

UPDATE = "03_returns_policy_update_2026.md"
OLD = "02_returns_policy_2024.md"

CATEGORY_GROUPS = {
    "clothing_footwear_home": {
        "clothing", "apparel", "fashion", "footwear", "shoes", "home", "kitchen",
        "home and kitchen", "home & kitchen", "home_kitchen", "furniture", "home decor",
    },
    "electronics": {
        "electronics", "electronic", "accessories", "electronics accessories", "mobile",
        "mobiles", "laptop", "laptops", "audio", "wearables", "computers", "gadgets",
    },
    "never_returnable": {
        "innerwear", "lingerie", "socks", "gift card", "gift cards", "giftcard",
    },
    "personal_care": {"beauty", "personal care", "cosmetics", "skincare", "grooming"},
}

WINDOWS = {"clothing_footwear_home": 15, "electronics": 10}

DEFECT_PATTERN = re.compile(
    r"\b(damag\w*|defect\w*|broken|break|cracked|crack|faulty|not working|doesn'?t work|"
    r"stopped working|dead on arrival|wrong (item|product|size sent|colou?r sent)|"
    r"incorrect item|missing part|torn|leak\w*|scratch\w*|dent\w*)\b",
    re.IGNORECASE,
)
DAMAGED_PATTERN = re.compile(r"\b(damag\w*|broken|cracked|crack|torn|dent\w*|scratch\w*|leak\w*)\b", re.I)


def category_group(category: str) -> str:
    c = category.strip().lower()
    for group, names in CATEGORY_GROUPS.items():
        if c in names:
            return group
    return "unspecified"


def classify_reason(reason: str) -> str:
    """'damaged' | 'defective_or_wrong' | 'change_of_mind'."""
    if DAMAGED_PATTERN.search(reason or ""):
        return "damaged"
    if DEFECT_PATTERN.search(reason or ""):
        return "defective_or_wrong"
    return "change_of_mind"


@dataclass
class Decision:
    eligible: bool
    outcome: str  # approved | rejected | manual_review
    reasons: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _item_decision(item: Item, days: int, reason_type: str) -> Decision:
    group = category_group(item.category)
    name = item.name

    if item.final_sale:
        return Decision(False, "rejected", [f"'{name}' was a Final Sale item and cannot be returned."],
                        [f"{UPDATE} § 4. Sale Items"])
    if group == "never_returnable":
        return Decision(False, "rejected", [f"'{name}' ({item.category}) is a non-returnable item."],
                        [f"{OLD} § 2. Non-Returnable Items"])
    if item.customised:
        return Decision(False, "rejected", [f"'{name}' is a customised/personalised product and cannot be returned."],
                        [f"{OLD} § 2. Non-Returnable Items"])
    if group == "personal_care":
        if item.opened is not False:
            return Decision(False, "rejected",
                            [f"'{name}' is a personal care/beauty product; opened products are non-returnable."],
                            [f"{OLD} § 2. Non-Returnable Items"])
        return Decision(False, "manual_review",
                        [f"Unopened '{name}': the current policy does not state a return window for this category."],
                        [f"{UPDATE} § 1. New Return Window"])
    if group == "unspecified":
        return Decision(False, "manual_review",
                        [f"The current return policy does not define a return window for category '{item.category}'."],
                        [f"{UPDATE} § 1. New Return Window"])

    # Damaged items reported within 48 hours: accepted, photo required.
    if reason_type == "damaged" and days <= 2:
        return Decision(True, "approved",
                        [f"'{name}' reported damaged within 48 hours of delivery."],
                        [f"{UPDATE} § 2. Damaged or Defective Items", f"{UPDATE} § 3. Return Pickup Charges"],
                        ["Upload at least one photo of the damage."])

    window = WINDOWS[group]
    cites = [f"{UPDATE} § 1. New Return Window"]
    if reason_type == "damaged":
        cites.append(f"{UPDATE} § 2. Damaged or Defective Items")

    if group == "electronics" and reason_type == "change_of_mind":
        return Decision(False, "rejected",
                        [f"'{name}' is electronics/accessories, which can only be returned if defective, "
                         f"damaged or wrong, not for change of mind."], cites)
    if days > window:
        return Decision(False, "rejected",
                        [f"'{name}' was delivered {days} days ago; the return window for this category "
                         f"is {window} days from delivery."], cites)

    reqs = [] if reason_type != "change_of_mind" else ["Item must be unused, with original tags and packaging."]
    if reason_type == "change_of_mind":
        cites.append(f"{OLD} § 1. Return Window")  # condition rule (unused, tags) still applies
    cites.append(f"{UPDATE} § 3. Return Pickup Charges")
    return Decision(True, "approved",
                    [f"'{name}' is within the {window}-day return window (delivered {days} days ago)."],
                    cites, reqs)


def check_return_eligibility(order: Order, reason: str, today: date) -> Decision:
    if order.status in {"return_requested", "returned", "refunded"}:
        return Decision(False, "rejected", [f"A return has already been requested for {order.order_id}."], [])
    if order.status in {"cancelled", "canceled"}:
        return Decision(False, "rejected", [f"{order.order_id} was cancelled, so there is nothing to return."], [])
    if order.status != "delivered" or not order.delivered_date:
        return Decision(False, "rejected",
                        [f"{order.order_id} has not been delivered yet (status: {order.status}). "
                         f"The return window starts from delivery."],
                        [f"{UPDATE} § 1. New Return Window"])

    days = (today - order.delivered_date).days
    reason_type = classify_reason(reason)
    decisions = [_item_decision(item, days, reason_type) for item in order.items]

    merged = Decision(
        eligible=all(d.eligible for d in decisions),
        outcome="approved",
        reasons=[r for d in decisions for r in d.reasons],
        citations=list(dict.fromkeys(c for d in decisions for c in d.citations)),
        requirements=list(dict.fromkeys(r for d in decisions for r in d.requirements)),
    )
    if not merged.eligible:
        merged.outcome = "rejected" if any(d.outcome == "rejected" for d in decisions) else "manual_review"
    merged.reasons.insert(0, f"Reason classified as: {reason_type.replace('_', ' ')}.")
    return merged
