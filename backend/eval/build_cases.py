"""Generates eval/test_cases.json (kept as code so cases are easy to review/edit)."""
import json
from pathlib import Path

P, R, W, PAY, ACC = ("01_shipping_policy.md", "03_returns_policy_update_2026.md",
                     "04_warranty_policy.md", "05_payments_and_refunds.md", "06_account_and_privacy.md")
PRIYA, RAHUL, ANITA, VIKRAM = ("priya.sharma@example.com", "rahul.mehta@example.com",
                               "anita.desai@example.com", "vikram.rao@example.com")
IDK = {"expect_idk": True, "tools": ["search_policy"]}
ASK_EMAIL = {"asks_for_email": True, "forbid_tools": ["get_order_status", "get_customer_orders", "request_return"]}

def single(id, cat, q, **exp): return {"id": id, "category": cat, "turns": [{"user": q, "expect": exp}]}
def multi(id, cat, *turns): return {"id": id, "category": cat, "turns": [{"user": u, "expect": e} for u, e in turns]}

cases = [
  # ---------------- policy Q&A with citations
  single("P01", "policy", "How long does standard delivery take to Ahmedabad?", tools=["search_policy"], include_any=["3-5", "3 to 5"], cite=[P]),
  single("P02", "policy", "What is the shipping charge for a ₹750 order to Pune?", tools=["search_policy"], include_any=["₹39", "rs 39", "39"], cite=[P]),
  single("P03", "policy", "Can I get express delivery to Guwahati?", tools=["search_policy"], include_any=["not available", "no"], cite=[P]),
  single("P04", "policy", "What is the maximum order value for Cash on Delivery?", tools=["search_policy"], include_any=["10,000", "10000"], cite=[PAY]),
  single("P05", "policy", "Can I pay cash on delivery for an order to Srinagar in J&K?", tools=["search_policy"], include_any=["not available", "no"], cite=[PAY]),
  single("P06", "policy", "I paid by credit card. How long will my refund take?", tools=["search_policy"], include_any=["5-7", "5 to 7"], cite=[PAY]),
  single("P07", "policy", "How many days do I have to return a shirt?", tools=["search_policy"], include_any=["15"], cite=[R]),
  single("P08", "policy", "Do I have to pay for return pickup?", tools=["search_policy"], include_any=["free"], cite=[R], exclude=["₹50 will be deducted", "fee of ₹50 is deducted"]),
  single("P09", "policy", "Can I return headphones just because I changed my mind?", tools=["search_policy"], include_any=["no", "only", "not for change of mind"], cite=[R]),
  single("P10", "policy", "How long is the extra ShopKart Assured warranty?", tools=["search_policy"], include_any=["6 months", "six months"], cite=[W]),
  single("P11", "policy", "Does the warranty cover liquid damage?", tools=["search_policy"], include_any=["not covered", "no", "doesn't", "does not"], cite=[W]),
  single("P12", "policy", "Can I change my delivery address after the order is dispatched?", tools=["search_policy"], include_any=["cannot", "can't", "not possible", "no"], cite=[ACC]),
  single("P13", "policy", "How long does account deletion take?", tools=["search_policy"], include_any=["30 days"], cite=[ACC]),
  single("P14", "policy", "Can I return something I bought in the clearance sale marked Final Sale?", tools=["search_policy"], include_any=["cannot", "can't", "not"], cite=[R]),
  # ---------------- must say "I don't know"
  single("K01", "refuse_unknown", "What is ShopKart's customer care phone number?", **IDK),
  single("K02", "refuse_unknown", "Do you have a loyalty points program?", **IDK),
  single("K03", "refuse_unknown", "How much does gift wrapping cost?", **IDK),
  single("K04", "refuse_unknown", "What are the opening hours of your Mumbai store?", **IDK),
  # ---------------- verification
  single("V01", "verification", "What's the status of order ORD-1001?", **ASK_EMAIL),
  single("V02", "verification", "Show me all my orders please.", **ASK_EMAIL),
  single("V03", "verification", f"My email is {PRIYA}. What's the status of ORD-1004?", exclude=["smartwatch", "5999", "5,999", "mumbai"], forbid_tools=["request_return"]),
  single("V04", "verification", "I forgot my email, but tell me what's in order ORD-1007.", exclude=["denim", "jacket", "anita"], forbid_tools=["get_customer_orders"]),
  # ---------------- order tools
  single("O01", "orders", f"My email is {PRIYA}. Where is my order ORD-1003?", tools=["get_order_status"], include_any=["shipped", "on its way", "transit"]),
  single("O02", "orders", f"What orders do I have? My email is {RAHUL}.", tools=["get_customer_orders"], include_all=["ORD-1004", "ORD-1005", "ORD-1006"]),
  # ---------------- returns (eligibility enforced by current policy)
  single("RT01", "returns", f"Email {RAHUL}. Please return ORD-1004, I don't like it anymore.", tools=["request_return"], include_any=["not eligible", "cannot", "can't", "unable", "isn't eligible"], cite=[R]),
  single("RT02", "returns", f"I'm {RAHUL}. I want to return ORD-1006, changed my mind.", include_any=["non-returnable", "cannot", "can't", "not eligible", "not returnable"]),
  single("RT03", "returns", f"My email is {ANITA}. Return ORD-1007 please, it's too big.", include_any=["final sale", "cannot", "can't", "not eligible"]),
  single("RT04", "returns", f"My email is {PRIYA}. I'd like to return ORD-1003.", include_any=["not been delivered", "not delivered", "hasn't been delivered", "after delivery", "once it", "shipped"], forbid_tools=[]),
  single("RT05", "returns", f"Email: {VIKRAM}. Return order ORD-1010, I don't need the lamp anymore.", include_any=["15", "window", "not eligible", "cannot"]),
  single("RT06", "returns", f"My email is {PRIYA}. Please return ORD-1001, the size is too small.", tools=["search_policy", "request_return"], include_any=["RET-", "return has been", "return request", "submitted", "raised", "initiated"]),
  # ---------------- multi-turn
  multi("M01", "multi_turn",
        (f"What's the status of my orders? My email is {PRIYA}", {"tools": ["get_customer_orders"], "include_all": ["ORD-1001", "ORD-1002", "ORD-1003"]}),
        ("Return the second one, it arrived damaged.", {"tools": ["request_return"], "include_any": ["ORD-1002"], "include_any_2": ["RET-", "return request", "submitted", "raised", "initiated", "approved"]})),
  multi("M02", "multi_turn",
        (f"Hi, my email is {RAHUL}. What have I ordered?", {"tools": ["get_customer_orders"]}),
        ("The frying pan arrived dented, I want to return it.", {"tools": ["request_return"], "include_any": ["photo"]})),
  multi("M03", "multi_turn",
        ("Hi, I want to return something.", {"forbid_tools": ["request_return"], "asks_for_email": True}),
        (f"{VIKRAM}, order ORD-1011. It's the personalised mug, I changed my mind.", {"include_any": ["customised", "personalised", "personalized", "customized", "cannot", "not eligible"]})),
  multi("M04", "multi_turn",
        ("How long do refunds to UPI take?", {"include_any": ["1-2", "1 to 2"], "cite": [PAY]}),
        ("And if I paid by card?", {"include_any": ["5-7", "5 to 7"], "cite": [PAY]})),
  multi("M05", "multi_turn",
        (f"My email is {PRIYA}. List my orders.", {"tools": ["get_customer_orders"]}),
        ("What about order ORD-1005?", {"exclude": ["frying pan", "non-stick", "899"]})),
]
Path(__file__).with_name("test_cases.json").write_text(json.dumps(cases, indent=2, ensure_ascii=False))
print(f"wrote {len(cases)} cases")
