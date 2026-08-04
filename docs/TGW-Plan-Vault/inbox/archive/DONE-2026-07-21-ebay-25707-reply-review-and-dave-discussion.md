# TIGWA REQUEST — Review and discuss eBay orphaned-offer support reply with Dave

**Date:** 2026-07-21
**From:** Tigwa, at Dave's direction
**To:** Claude
**Status:** Review/discussion request only. Do not send any external message, mutate eBay data, make API calls, access credentials, or close/change the support register from this note.

## Request

Please review the proposed reply below to eBay Developer Support case `260719-000018` and discuss it directly with Dave before any external submission.

Focus on:

1. factual precision and whether the response accurately distinguishes an invalid literal historical identifier from a request to select a different valid current SKU;
2. whether it gives eBay enough information to locate the inaccessible object server-side without revealing credentials, private identifiers, or unnecessary internal implementation detail;
3. the requested remedies: server-side purge, offer-ID recovery plus a supported remediation operation, or another specific supported path;
4. whether the urgency/business-impact wording is sufficiently firm but professional; and
5. any missing evidence, question, or safe clarification that should be added before Dave sends it.

The reply must remain a draft until Dave explicitly chooses to submit it.

## eBay's reply received by Dave (2026-07-19)

> Hello Dave,
>
> Thank you for your response.
>
> We reviewed the details and it appears the SKU you used is incorrect, which is why you’re encountering the error. Please provide a valid SKU. You can retrieve the correct SKU values from your inventory using the getInventoryItems method, and then use the appropriate SKU in your request.
>
> Reference:
>
> https://developer.ebay.com/develop/api/sell/inventory_api#sell-inventory_api-inventory_item-getinventoryitems
>
> Once you update the request with the correct SKU, the issue should be resolved.
>
> Additionally, please share how you retrieved the SKU value you’re currently using.
>
> Best regards,
>
> eBay Developer Support
> Customer via CSS Web
> July 19 2026, 12:58:23 PM

## Evidence-grounded proposed reply

**Subject: Case 260719-000018 — clarification on the invalid historical SKU and request for server-side resolution**

Hello eBay Developer Support,

Thank you for your reply.

I understand the current Inventory API SKU validation requirements and agree that the literal SKU at issue does not meet those requirements. However, the problem in this case is not that we need help selecting a current valid SKU from our inventory. The problem is that there appears to be a pre-existing offer/object associated with a literal SKU that is now invalid under the current API validation rules, and the current API prevents us from locating or resolving that object ourselves.

For clarity, the literal SKU associated with the affected object is:

`Murder on the Middle Fork by Don Ian Smith and Naida West`

This value is 57 characters long and contains spaces. It therefore fails the current Inventory API validation rule described in error 25707: SKU values must contain only alphanumeric characters and must not exceed 50 characters.

This is exactly why the normal `getInventoryItems` guidance does not resolve the case.

### How we obtained the SKU value

The above value was not newly selected from our inventory, guessed, or generated for this request. It is the literal identifier attached to the existing problematic offer/error record that prompted this support case.

We did not obtain it from `getInventoryItems`, because it is not a valid current Inventory API inventory-item SKU. We are reporting the exact literal value associated with the affected object so that eBay can locate the historical record on the server side.

### Verification performed after receiving the original guidance

To make sure we were not relying on stale information, we performed a fresh verification on July 19, 2026.

1. **Direct offer lookup using the exact literal value.** We attempted `GET /sell/inventory/v1/offer?sku=<exact literal SKU above>`. The API returned HTTP 400 with error 25707, stating that the SKU is invalid because it contains non-alphanumeric characters and exceeds the 50-character limit. The request is rejected at SKU validation before the API can perform an offer lookup; therefore we cannot obtain an offer ID, inspect the object, update it, or delete it through this path.

2. **Full inventory-item enumeration.** We performed a complete paginated `getInventoryItems` sweep: 98 pages retrieved, 19,509 inventory items examined, zero request errors, and the exact literal SKU was not present in any returned inventory item.

Accordingly, we have already followed the suggested inventory-enumeration path. It confirms that this literal value is not a current retrievable inventory-item SKU.

### Why substituting another valid SKU would not fix this case

A different valid SKU would identify a different current inventory item. It would not identify, remove, repair, or otherwise resolve the inaccessible historical object associated with the literal invalid value above.

The current problem is therefore not “which valid SKU should we use in a new request?” The problem is “how can the existing affected object be located and resolved when the API rejects its own stored identifier before lookup?”

### Requested eBay action

Please locate the affected object internally using the seller account associated with this support case, the marketplace associated with that account, and the exact literal SKU above.

Once you locate it, please provide one of the following resolutions:

1. Remove/purge the inaccessible object on the server side; or
2. Provide the associated offer ID and a supported documented API operation or other procedure that will allow us to resolve it; or
3. Provide another specific, supported remediation path if neither removal nor offer-ID recovery is possible.

We would also appreciate an explanation of how an object bearing a value that fails the present Inventory API SKU validation could have been created or retained. In particular, please check whether this could relate to a legacy creation route, bulk-import path, migration behavior, or another historical system path.

### Operational impact

This is not merely a validation question for a new listing. The inaccessible object has interrupted normal bulk offer reconciliation since before July 2, 2026.

As of July 18, 2026, it had caused 774 consecutive fallback runs. The resulting fallback behavior is slower and prevents normal reconciliation from completing through the standard bulk path.

We are not requesting access to credentials, internal data, or undocumented behavior. We are requesting help resolving one specific existing object that cannot be addressed through the public Inventory API because its literal stored identifier is rejected before the object can be looked up.

Thank you for reviewing the evidence above. Please escalate this case to the team able to locate the affected record internally and either purge it or provide the necessary identifying/remediation information.

Best regards,

Dave Buko
