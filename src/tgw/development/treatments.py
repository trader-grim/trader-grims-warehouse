"""Development-domain treatment contracts; never item or provider work."""

from tgw.workflow_kernel.contracts import EffectClass, FingerprintResult, Requirement, TreatmentContract

_RECEIPT = "receipt/tgw-development/v1"

CODEX_IMPLEMENT = TreatmentContract("codex-implement", "1", (Requirement("implemented", (FingerprintResult.FALSE,)),), ("implemented", "tested"), ("source_files",), ("code.implement",), EffectClass.LOCAL, _RECEIPT)
CLAUDE_REVIEW = TreatmentContract("claude-review", "1", (Requirement("implemented", (FingerprintResult.TRUE,)), Requirement("tested", (FingerprintResult.TRUE,)), Requirement("linted", (FingerprintResult.TRUE,))), ("reviewed",), ("source_files",), ("code.review",), EffectClass.LOCAL, _RECEIPT)
CONTROLLER_VERIFY = TreatmentContract("controller-verify", "1", (Requirement("implemented", (FingerprintResult.TRUE,)),), ("tested", "linted", "controller_verified"), ("source_files",), ("code.verify",), EffectClass.LOCAL, _RECEIPT)
HERMES_STITCH = TreatmentContract("hermes-stitch", "1", (Requirement("reviewed", (FingerprintResult.TRUE,)), Requirement("controller_verified", (FingerprintResult.TRUE,))), ("committed",), ("source_files",), ("code.stitch",), EffectClass.LOCAL, _RECEIPT)

CODING_TREATMENTS = (CODEX_IMPLEMENT, CLAUDE_REVIEW, CONTROLLER_VERIFY, HERMES_STITCH)
