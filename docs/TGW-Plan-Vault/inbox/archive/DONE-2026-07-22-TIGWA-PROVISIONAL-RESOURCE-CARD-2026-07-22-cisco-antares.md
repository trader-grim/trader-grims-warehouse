# TIGWA PROVISIONAL RESOURCE CARD — Cisco Foundation AI Antares vulnerability-localization family

**Catalog state:** `source-verified / provisionally described / not admitted`
**Catalog owner:** Tigwa (librarian)
**Requested by:** Dave, 2026-07-22
**Scope:** defensive, read-only vulnerability localization only

## Identity and source evidence

| Field | Current evidence |
|---|---|
| Publisher identity | Hugging Face organization `fdtn-ai` is presented as Cisco Foundation AI; model cards name a Cisco contact and Cisco privacy policy. |
| Resources | `fdtn-ai/antares-350m` and `fdtn-ai/antares-1b`; an Antares-3B is described as a future family member, not available/verified here. |
| Access state | **Gated.** Public model pages require a signed-in Hugging Face user to accept contact-sharing conditions; requests are manually reviewed. No TGW download or account request has occurred. |
| Claimed purpose | Terminal-agent vulnerability localization: given a CWE category, inspect a repository and return candidate vulnerable files. |
| Claimed operation | Up to 15 terminal calls, then `submit_vulnerable_files` or `submit_no_vulnerability_found`; output can be human-readable, JSON, or SARIF through Cisco's described CLI. |
| Vendor performance claim | Antares-1B reports File F1 0.209 on Cisco's 500-task VLoc Bench. This remains a publisher claim until independently reproduced on a relevant controlled corpus. |

Primary sources: https://huggingface.co/fdtn-ai/antares-350m and https://huggingface.co/fdtn-ai/antares-1b (captured 2026-07-22).

## Provisional job description

**Job:** `defensive.vulnerability-localization.candidate-files`

Given a frozen, deliberately supplied code snapshot and a known CWE/CVE-style task description, produce a ranked **candidate file list** for human/security-review follow-up.

Allowed:
- read only the mounted repository;
- invoke an explicit allowlist of inspection commands in an isolated container;
- emit reasoning trace, command transcript, candidate paths, confidence, runtime, and resource use;
- return JSON/SARIF as a draft finding.

Forbidden:
- network access;
- repository writes, git commits, package installation, builds, service starts, outbound upload, credential access, shell escape, or access outside the mounted snapshot;
- declaring a repository clean, a vulnerability confirmed, a patch correct, or a release safe;
- autonomous ticket, PR, alert, or production mutation.

**Authority:** advisory only. A candidate is a finding for the security/review queue, never a verified vulnerability or a remediation instruction.

## Admission and first-track-record protocol

1. Do not request gated access or download until Dave approves the contact-sharing terms and the intended host/storage location.
2. On approved acquisition, record the exact Hugging Face revision, license/terms, every artifact hash, and size. Store large immutable artifacts through git-annex; keep the small manifest, contract hash, and annex key in Git.
3. Execute only in a disposable network-disabled container, mounted read-only to a purpose-selected fixture or frozen code snapshot. Capture container image digest, command allowlist, model/configuration, prompt, tool transcript, output, wall time, CPU/RAM, and all artifact hashes.
4. Start with the 350M variant on a synthetic/known-label fixture. Only try 1B if the 350M harness proves safe and the host’s measured latency/RAM is acceptable. The local host currently has 4 CPU cores and about 13 GiB available RAM; this is not runtime evidence or a throughput claim.
5. Compare output to known ground-truth files. Record precision/recall, false positives/negatives, command-boundary behavior, and review burden.
6. Append the trial record and hashes to the remote immutable log. Only successful measured trials produce the first actual resume entry or expand the job description.

## Curation status

This is a high-confidence **fit hypothesis**, not a high-confidence worker. Its publisher/source and narrow job are credible; its permission terms, artifact identity, local runtime behavior, and performance on TGW-relevant code are all still unproven.

The resume begins empty except for quoted vendor claims. The library, not Cisco, writes the proven record.
