# Evidence note — 2017 photo-throughput cohort is the practical TGW success criterion

**From:** Tigwa, librarian
**To:** Claude
**Status:** operator-provided historical evidence; validate quantitatively before causal claims or target tuning
**Date:** 2026-07-22

## Dave's observed historical result

In 2017, for approximately five months, a dedicated helper used an older camera app and `tgw.source` to photograph items for about five hours per day. Dave reports that business income tripled within a month and rose to nearly five times the prior level during that period.

The point is not nostalgia for an old app. This is the strongest known operational proof that the bottleneck worth removing is Dave's non-photography busywork. The intended outcome of the present TGW/operator/AI work is to make it practical for Dave to photograph roughly **250 items/day**, by reliably absorbing or simplifying the surrounding intake, data, review, listing, and communication work.

## Initial direct data indication

The present catalog exposes time-encoded TGW SKUs such as `tgw201701040108133`, `tgw201701042218279`, and neighboring timestamped records. A read-only catalog sample returned a dense sequence of January 2017 records. This supports the feasibility of reconstructing the historical activity cohort from item data, but it does **not** yet prove the exact five-month boundary, hours, revenue multiplier, or causation.

## Evidence classification

- **Operator observation:** helper role, older camera-app/`tgw.source` workflow, roughly five months × five hours/day, reported income result, and 250/day target.
- **Catalog evidence available for audit:** timestamped SKU sequence and ItemData/camera-era records.
- **Still required for quantified proof:** exact cohort date window; daily/hourly item-creation or photo timestamps; camera-app/source provenance where retained; eBay sales/revenue history and lag-aware comparison window; confounders such as sourcing volume, pricing, inventory mix, and seasonality.

No model should turn the income result into a universal causal claim merely because an increased workflow coincided with it. It is already decisive operator evidence for what to optimize; the later audit measures magnitude and operating envelope.

## Product/operations implication

Treat "photography capacity" as a first-class outcome metric, not a side effect of a feature list. Candidate operational measures:

1. photo-ready items completed per day and per focused hour;
2. time from physical item availability to photographed/identified/review-ready state;
3. backlog before and after photography;
4. downstream listing completion and error/rework rate for the photographed cohort;
5. lag-aware sales/revenue realization, clearly separated from intake throughput.

A Flutter/KFMAWI surface, camera workflow, clip route, Tasker, Radar, agent/mailbox work, and automation are valuable only insofar as they remove friction from this loop, preserve operator control, or make its evidence visible. They must not add ceremonial communication work that takes Dave away from photographing.

## Bounded next evidence packet (not yet authorized to build)

Perform a read-only 2017 cohort reconstruction: preserve a query/capture manifest; identify the exact date range from timestamped ItemData/camera records; calculate daily/hourly SKU/photo volume against matched pre/post comparison windows; join a separately retained eBay revenue/sales series with explicit lag assumptions; report anomalies and confounders. Stage results for Dave review before turning them into capacity targets or a performance claim.

No catalog, listing, pricing, camera, credential, or production mutation is authorized by this note.
