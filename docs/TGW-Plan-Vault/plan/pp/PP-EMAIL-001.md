## PP-EMAIL-001 — Email Integration

### Problem
eBay sends automated emails for: buyer messages, order notifications, case alerts, policy
violations, and payment updates. Currently these require manual Seller Hub monitoring.
Outgoing communication to buyers is also manual.

### Inbound — auto-processing
- Monitor eBay buyer message inbox (eBay Messages API or email forward to IMAP inbox)
- Parse and categorize: order question, tracking request, return request, feedback reminder
- Route to TGW: match to order → attach to item JSON event log; generate suggested response
- Alert operator for messages requiring human response; auto-reply for simple FAQ patterns
- Integration: eBay Messaging API (part of `sell.fulfillment` scope family)

### Outbound — free SMTP
- Gmail "Send Mail As" feature: use a Gmail account to send from a custom address
  (e.g. `support@yourdomain.com`) via Gmail SMTP without a paid mail server
- Investigate: `smtplib` + Gmail SMTP with app password; or `gmail-send` Python wrapper
- Use case: order confirmation, tracking number follow-up, buyer communication

### Dependencies
- eBay messaging scope (new keyset request covers this)
- Gmail account with "Send Mail As" configured
- IMAP library: `imaplib` (stdlib) or `imapclient` package

---

