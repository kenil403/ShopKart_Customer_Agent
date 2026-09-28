// Frequently asked questions shown on the main screen, grouped by topic.
export const FAQ = [
  { topic: "Delivery", items: [
    "How long does delivery to Ahmedabad take?",
    "What is the shipping charge for a ₹700 order to Pune?",
    "Can I change my delivery address?",
    "How do I track my order?",
  ]},
  { topic: "Returns & refunds", items: [
    "Can I return headphones if I changed my mind?",
    "Is return pickup free?",
    "How long does a refund to my card take?",
    "Can I return a Final Sale item?",
  ]},
  { topic: "Payments & warranty", items: [
    "Is Cash on Delivery available?",
    "What does the ShopKart Assured warranty cover?",
    "Does the warranty cover liquid damage?",
  ]},
  { topic: "Your orders", items: [
    "Where is my order?",
    "I want to return an item.",
    "Show me my recent orders.",
  ]},
];

// Extra group in the developer view (?dev=1): a scripted demo using sample accounts.
export const DEMO_FAQ = { topic: "Demo script", items: [
  "What's the status of my orders? My email is priya.sharma@example.com",
  "Return the second one, it arrived damaged.",
  "What about ORD-1004?",
  "What's your customer care number?",
]};

export const DEMO_ACCOUNTS = [
  ["priya.sharma@example.com", "ORD-1001 to 1003"],
  ["rahul.mehta@example.com", "ORD-1004 to 1006"],
  ["anita.desai@example.com", "ORD-1007 to 1009"],
  ["vikram.rao@example.com", "ORD-1010 to 1012"],
];
