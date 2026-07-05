# DONE — ebay_draft 402 dead-letter pile fully drained (s45, 2026-07-04/05)

The pile born from the OpenRouter-402 billing gap (3,239 dead-letters as of
s43) is fully processed: morning batches + post-provider-flip drain + final
2,658-job pass = ~6,500 jobs run today, 99.9%+ success, ~$1.08 total key
spend. Provider flip (todo #1144) removed the 429+40s tax mid-day — drain
rate went ~10x.

Only surviving failures: 4 corrupt-photo SKUs (tgw201601011311007,
tgw201601011312446, tgw201707050929532, tgw201707151156404) — Feb 2022
migration truncation, on the #1145 recovery roster; fleet-wide integrity
sweep running on a1131 (#1154). The ~113 non-402 historical dead-letters
were re-run in the final pass and either succeeded or re-failed into the
same corrupt-photo class.

Follow-on: these ~2,650 items now have fresh drafts pending operator review
— they enter the normal ready/dole flow, NOT auto-published (C9).
