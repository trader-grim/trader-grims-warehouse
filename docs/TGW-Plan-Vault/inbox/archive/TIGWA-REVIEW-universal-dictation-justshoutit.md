# TIGWA review request — Universal Dictation and Voice Fabric for JustShoutIt

**Status:** review requested  
**Plan:** PP-INTAKE-004  
**Related todos:** #1426 (Tigwa), #1327 (Claude STT research), #1353 (verified Hermes voice baseline)  
**Date:** 2026-07-15

## Artifacts for review

- `docs/TGW-Plan-Vault/inbox/TIGWA-RESEARCH-universal-dictation-justshoutit.md`
- `docs/TGW-Plan-Vault/inbox/TIGWA-RESEARCH-universal-dictation-justshoutit.yaml`

## What this establishes

The recommendation extends—not replaces—the existing JustShoutIt foundation: Voice In, xmouse's SSH macro pad, a tiny local web page, and the active Tasker camera-event server over KDE Connect clipboard relay.  The legacy `COMMAND:`, `DATA:`, `TEMPLATE:`, and location-event forms are the current remote-camera control path; the later PP-EVENTD-001 `clip-route` design is not a prerequisite for it.

Dave clarified the primary outcome: JustShoutIt is a fast assisted-capture loop, not a macro system. Speech plus Syncthing-delivered early photos produce provisional identity/candidate facts; the operator supplies only required missing facts before location; camera completion into `incoming/newitems/` drives final merge/refinement and a listing-ready review candidate.  The existing documented six-photo batch remains the strong-pass benchmark; an earlier configurable provisional pass after the first couple of photos is proposed for review.

The recommendation is a common local-LAN Voice Fabric with typed capture and output adapters for the photo booth, a1131, and executive tablet.  It separates:

1. focused-field dictation;
2. structured JustShoutIt attribute entry into PP-INTAKE-004's manual write surface; and
3. Tigwa/Leotha conversation.

The proposed connected path is the already-verified Hermes Groq STT + Edge TTS stack.  The existing proven `ffmpeg → whisper-cli` path remains the local/offline fallback.  `whisper.cpp` is the first offline benchmark candidate, not an approved installation.  Desktop insertion is updated to clipboard staging plus validated `ydotool` Ctrl+V per workstation.  VOSK4Tasker is explicitly excluded as a platform dependency because its repository is archived.  For later Android wake/voice, the preferred FOSS evaluation is a native `sherpa-onnx` foreground voice service; Tasker/Termux stay orchestration and PTT fallback, not the always-listening runtime.

## Review questions

1. Is the “one Voice Fabric, three typed modes” boundary correct for JustShoutIt and the wider Android/desktop estate?
2. Do Dave and Claude agree that Groq is the primary connected path and the existing whisper-cli pipeline remains the first fallback, pending real photo-booth benchmarks?
3. Is explicit armed-field injection plus visible transcript/undo adequate before universal desktop typing is enabled?
4. Confirm the desired audio-retention posture.  PP-INTAKE-004 requires transcript/guess/correction records; raw-audio retention is intentionally left undecided.
5. May Phase 0 begin after the remote photo-station microphone endpoint is inventoried, or should Claude's #1327 recommendation gate it first?

## Verification evidence

- a1131 live inspection found PipeWire 1.4.7 in Dave's KDE Wayland session, Built-in Audio Analog Stereo capture/playback, active `ydotoold`, and `db` membership in the `ydotool` group.
- a1131 is a 4-core Intel i5-2400S with 19 GiB RAM and no detected GPU-compute utility; local STT must be benchmarked rather than assumed responsive.
- TGW todo #1353 records a successful direct Hermes Groq `whisper-large-v3-turbo` test and verified Edge TTS on 2026-07-12.
- PP-INTAKE-004 confirms the proven existing `ffmpeg → whisper-cli` pipeline and the JustShoutIt persistence/operator-precedence requirements.
- No source, Android/Tasker configuration, microphone settings, gateway, flake, or production data was changed.
