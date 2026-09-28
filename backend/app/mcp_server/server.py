"""ShopKart Orders MCP server.

Exposes exactly the three tools from the brief, with the brief's signatures:
    get_order_status(order_id)
    get_customer_orders(email)
    request_return(order_id, reason)

The server is the "system of record": it owns order data and enforces the
return policy (request_return refuses ineligible orders even if the agent asks).
Email verification happens in the agent layer (see app/agent/tools.py) so the
LLM never sees order details for an unverified customer.

Run standalone (stdio):  python -m app.mcp_server.server
Inspect with:           mcp dev app/mcp_server/server.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# Allow `python app/mcp_server/server.py` as well as `-m`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app import config  # noqa: E402
from app.orders_repo import OrdersRepo  # noqa: E402
from app.policy.return_rules import check_return_eligibility  # noqa: E402

mcp = FastMCP("shopkart-orders", log_level="WARNING")
repo = OrdersRepo(config.ORDERS_FILE)


def _log_return(entry: dict) -> None:
    """Append to the returns log. Never fails the return itself: a missing folder is
    created, and any other file problem is only reported on stderr."""
    try:
        config.RETURNS_LOG.parent.mkdir(parents=True, exist_ok=True)
        log = json.loads(config.RETURNS_LOG.read_text(encoding="utf-8")) if config.RETURNS_LOG.exists() else []
        log.append(entry)
        config.RETURNS_LOG.write_text(json.dumps(log, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not write returns log {config.RETURNS_LOG}: {exc}", file=sys.stderr)


@mcp.tool()
def get_order_status(order_id: str) -> dict:
    """Get the status and details of a single order by its order ID (e.g. ORD-1001).
    The result includes `customer_email`, which the caller must compare with the
    email the customer provided before sharing anything."""
    order = repo.get(order_id)
    if not order:
        return {"found": False, "order_id": order_id}
    return {"found": True, "customer_email": order.email, "order": order.public_view()}


@mcp.tool()
def get_customer_orders(email: str) -> dict:
    """List all orders placed with the given registered email address, in a stable
    numbered order (position 1, 2, 3...) so customers can refer to 'the second one'."""
    orders = repo.by_email(email)
    return {
        "email": email.strip().lower(),
        "count": len(orders),
        "orders": [{"position": i, **o.public_view()} for i, o in enumerate(orders, start=1)],
    }


@mcp.tool()
def request_return(order_id: str, reason: str) -> dict:
    """Request a return for an order. The server checks eligibility against the
    CURRENT return policy (2026 update + unchanged 2024 rules) and only creates the
    return if the order is eligible. Returns the decision and the policy sections used."""
    order = repo.get(order_id)
    if not order:
        return {"success": False, "order_id": order_id, "error": "order_not_found"}

    decision = check_return_eligibility(order, reason, config.today())
    result = {"order_id": order.order_id, "customer_email": order.email, **decision.to_dict()}

    if decision.eligible:
        return_id = f"RET-{order.order_id.split('-')[-1]}-{datetime.now().strftime('%H%M%S')}"
        order.status = "return_requested"  # in-memory for this server session
        _log_return({"return_id": return_id, "order_id": order.order_id, "reason": reason,
                     "created_at": datetime.now().isoformat(timespec="seconds")})
        result["citations"].append("02_returns_policy_2024.md § 3. How to Request a Return")
        result.update(success=True, return_id=return_id,
                      next_steps="Pickup will be scheduled within 2 business days. Pickup is free.")
    else:
        result.update(success=False)
    return result


if __name__ == "__main__":
    mcp.run()  # stdio transport
