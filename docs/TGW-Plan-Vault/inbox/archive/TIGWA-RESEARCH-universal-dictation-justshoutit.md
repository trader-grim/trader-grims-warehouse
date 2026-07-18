# Universal Dictation and Voice Fabric for JustShoutIt

**Status:** research recommendation — no systems changed  
**Owner:** Tigwa  
**Plan:** PP-INTAKE-004; complementary to Claude's existing todo #1327 (STT approach research)  
**Date:** 2026-07-15

## Decision in one sentence

Treat JustShoutIt as a **fast assisted-capture loop**, not a remote-macro system: operator speech supplies early metadata, Syncthing-delivered queue photos trigger an early identity pass, the operator supplies only the missing required facts before location, and capture completion/refinement produces a listing-ready candidate.  The local-LAN voice layer is one input adapter within that loop; it uses the proven Hermes Groq STT + Edge TTS connected path, retains `ffmpeg → whisper-cli` as offline/evidence-preserving fallback, and assesses `whisper.cpp` only as a lightweight interactive local engine.

## Existing JustShoutIt foundation

JustShoutIt began as an operational composition rather than a new voice product: **Voice In** provided browser dictation; **xmouse** acted as the tablet SSH macro pad; a **tiny local web page** provided the local control surface; and the existing **Tasker camera-event server** received `COMMAND:`, `DATA:`, `TEMPLATE:`, and location events through the KDE Connect clipboard relay.  The camera HUD consumes those events for remote camera operation.  This active Tasker/KDE channel is the existing command transport to preserve and progressively type—not a speculative future replacement.

The later PP-EVENTD-001 `clip-route` design is a separate planned cross-device routing layer.  It may normalize or extend this path later, but it is not required for the current camera event flow.

## Why this is needed

Voice In remains a useful working Chrome fallback at the photo booth, but it only types into Chrome.  JustShoutIt needs two different things:

1. **Universal dictation:** place the operator's exact transcript into the currently armed field in any supported desktop or tablet surface.
2. **Semantic voice entry:** recognize listing attributes, attach them to the active item, show the proposed value, preserve provenance, and write through the *same* attribute interface used by manual entry.

PP-INTAKE-004 settles that JustShOutIt is not a parallel write path: voice-parsed attributes land through the native Kotlin intake app's manual attribute-write surface.  Operator speech wins over AI inference for a field the operator addressed.  Transcripts, guesses, and operator corrections are permanent learning data.

## Current verified estate

| Finding | Evidence | Consequence |
|---|---|---|
| Hermes Groq `whisper-large-v3-turbo` direct test and Edge TTS were already verified on 2026-07-12. | TGW todo #1353 | Connected voice chat needs no new STT/TTS provider decision. |
| PP-INTAKE-004 already has a proven `ffmpeg → whisper-cli` path, currently writing suggestions globally. | PP-INTAKE-004 §Voice/STT approach | Reuse it item-scoped; do not replace working plumbing prematurely. |
| a1131 is an Intel i5-2400S (4 cores), 19 GiB RAM, no detected GPU-compute tool. | Live inspection, 2026-07-15 | Benchmark CPU local models; do not promise low latency from a large local model. |
| Dave's KDE Wayland desktop session has PipeWire, an active Built-in Audio source/sink, and `ydotoold`.  `db` is in the `ydotool` group. | Live inspection, 2026-07-15 | A desktop capture/client and focused-window text insertion are plausible on a1131.  They must run in Dave's desktop/audio context or use a deliberately brokered service. |
| Tigwa's non-desktop session cannot directly use Dave's PipeWire/audio session. | Live inspection, 2026-07-15 | Do not make the agent session own the physical microphone. |
| The photo-booth remote microphone's connection type and host are not yet verified. | Not yet inventoried | Its adapter is a discovery/benchmark item, not an assumed USB device. |
| LAN Mouse already shares barcode scanner input across both Linux machines. | Dave-provided context | Treat barcode HID and audio transport as separate planes; do not try to tunnel microphone audio through LAN Mouse. |

## Assisted capture loop — Dave clarification, 2026-07-15

```text
operator says item facts through JustShoutIt
  + camera sends photos into the synced queue
        ↓
Syncthing delivers the first photos to TGW
        ↓
early identity pass: identify visible item and populate only candidate facts
        ↓
operator sees filled facts + focused missing-required prompts
        ↓
operator supplies required details, then assigns/confirms location
        ↓
camera completes/wraps the bundle into `incoming/newitems/`
        ↓
finalization: merge accumulated facts, complete-photo refinement, validation
        ↓
listing-ready candidate — explicitly reviewable, never silently published
```

This is deliberately two-pass.  “After a couple of photos” should trigger a **provisional** identity result as soon as the configured minimum is reached; the currently documented six-photo batch remains the stronger/full identification threshold.  The full capture completion triggers `ai_reidentify` and reconciliation, rather than discarding the early work.  Operator-supplied facts take precedence over AI candidates, and required-field prompts must be limited to facts needed to move the item forward.

The session needs a capture/session ID independent of SKU so that speech and early photos can accumulate before the final item/SKU relationship is fully settled.  Syncthing remains the photo/data transport, not the decision authority; TGW applies the identity, merge, validation, and audit logic.

## Architecture

```text
[photo-booth remote mic] ─┐
[a1131 desktop PTT] ─────┼── capture adapters ── authenticated LAN Voice Gateway
[executive tablet] ──────┘                                 │
                                                           ├─ connected STT: Hermes / Groq
                                                           ├─ local STT: existing whisper-cli or evaluated whisper.cpp
                                                           ├─ JustShoutIt parser + active-item context
                                                           └─ Edge TTS / local-TTS response selector
                                                                      │
                  ┌───────────────────────────────────────────────────┴─────────┐
                  │                                                             │
          dictation adapter                                           semantic adapter
     armed focused field only                              candidate attributes + confidence + evidence
     desktop: clipboard + validated ydotool Ctrl+V          Kotlin app: native field/model update
     tablet: native app/Tasker UI                           same manual attribute-write contract
```

The gateway is a narrow typed service, not a remote-shell or arbitrary-input channel.  A request carries device identity, a short-lived request ID, timestamp/expiry, audio or transcript, an optional active-item/capture-session ID, and the requested mode.  It returns transcript, engine/provenance, confidence/uncertainty, candidate result, and optional audio response.

### Three deliberately separate modes

| Mode | Example | Result | Safety rule |
|---|---|---|---|
| `dictate` | “excellent condition, light surface wear” | Transcript into the one visibly armed field | Never inject merely into whichever window happens to be focused. |
| `attribute` | “size large, color burgundy, material wool” | Structured candidate attributes for the active item | Show/confirm ambiguous, high-impact, or multi-item changes; preserve correction. |
| `converse` | “Tigwa, what have we captured for this item?” | Hermes answer, optionally spoken through Edge TTS | No inventory mutation from casual conversation.  Consequential action needs explicit confirmation. |

This prevents the bad failure mode where a conversational transcript silently becomes a listing write.

## Platform choices

| Component | Status | Best role | Recommendation |
|---|---|---|---|
| Existing Groq STT + Edge TTS via Hermes | **Already proven** | Rich connected conversation and accurate short dictation | **Primary connected path.** Add free-tier/rate-limit telemetry and fallback. |
| Existing `ffmpeg → whisper-cli` | **Already proven in TGW plan** | Offline/fallback transcription and item-scoped evidence | **Keep and redirect**, rather than replace.  Evaluate latency/accuracy on actual photo-booth speech. |
| `whisper.cpp` | Active upstream MIT project; C/C++ local Whisper implementation with VAD and native mic-streaming examples | Candidate lightweight interactive local engine | **First offline benchmark.** Start PTT-release with `base.en`; compare `small.en` only if TGW vocabulary accuracy warrants the extra CPU/RAM. |
| `faster-whisper` | Active upstream MIT Python/CTranslate2 implementation | Candidate server engine if a suitable CPU/GPU host is chosen later | **Secondary evaluation.** Do not add a Python service until it beats the existing pipeline on measured latency/operations burden. |
| `sherpa-onnx` native Android service | Active Apache-2.0 Android-capable runtime: KWS, VAD, streaming ASR and TTS | Tablet’s long-term local wake/voice satellite | **Preferred later tablet runtime.** It can evaluate the four selected phrases locally; a native foreground service, not Tasker, owns the microphone. |
| Porcupine | Maintained Android-native custom wake-word SDK | Faster proprietary-account alternative to Sherpa KWS | **Optional evaluation.** Offline inference still requires AccessKey/custom-model provisioning; do not embed credentials in an APK/repo. |
| Vosk engine/server | Current offline grammar/STT option; server offers WebSocket/gRPC/WebRTC transports | Small offline command grammar or degraded mode | **Use only for bounded commands/fallback**, not as the default rich listing-dictation engine. |
| VOSK4Tasker | Archived; last code push 2021 | Temporary experimentation only | **Do not make it a platform dependency.** |
| Tasker `Get Voice` / Android system STT | Device/Google-dependent and not designed for continuous recognition | Tablet fallback or quick prototype | **Fallback only.** It is not the common multi-device STT policy. |
| AudioRelay | Proprietary Android→Linux microphone transport | Fast temporary remote-mic proof of path | **Optional Phase-0/1 convenience trial only.** It is not the Voice Fabric/control boundary; validate LAN/security/privacy before operational use. |
| Voice In Chrome extension | Working current user tool, but browser-only | Immediate photo-booth fallback | **Retain** while the universal path is proven; no expansion. |
| Talon / generic voice-computer-control products | Powerful but command-language/training-heavy | Hands-free accessibility or specialist desktop control | **Not the JustShoutIt foundation.** It solves a broader problem than reliable item dictation. |
| Wake-word frameworks | `sherpa-onnx` is the preferred FOSS tablet candidate; Porcupine is the account-backed alternative | Later local wake-word gate | **Evaluate only after PTT works.** A native Android foreground microphone service owns the always-listening lifecycle; Tasker/Termux remain orchestration/fallback glue. |

## Recommended rollout

### Phase 0 — instrument and benchmark before installation decisions

1. Identify the photo mic endpoint: host/device, connection type, gain/noise conditions, and whether it can provide a push-to-talk event.
2. Capture a consented, redacted test corpus at the photo booth and a1131: normal speech, item brands, colors, materials, sizes, SKU/location patterns, background noise, interruptions, and barcode-scanner activity.
3. Compare the already-proven Groq route, current whisper-cli route, and `whisper.cpp` candidate on the same clips.  Record transcription errors, time-to-first-result, final-result latency, corrections, and failure/retry behavior.
4. Validate a **clipboard stage + `ydotool` synthetic Ctrl+V** insertion in a disposable focused text field within Dave's KDE Wayland session.  Validate it separately on each Linux workstation: LAN Mouse sharing does not make text insertion cross-host.  Do not test by typing into a live listing or arbitrary focused application.
5. Define the UI “armed field” indicator, cancel/undo action, and a dedicated physical/HID PTT or desktop press/release binding before enabling dictation injection.
6. If the photo mic turns out to be Android-hosted, evaluate AudioRelay only as a temporary paired-LAN virtual-microphone transport before designing the native capture adapter.

**Exit gate:** select the engine/policy from measured TGW vocabulary accuracy and reliability, not generic benchmarks.

### Phase 1 — universal push-to-talk dictation

- A large on-screen PTT action at the photo-booth and executive tablet; a desktop hotkey/physical PTT where appropriate.
- Each capture adapter sends a short utterance to the gateway; it returns transcript and provenance.
- The desktop adapter inserts only into an explicitly armed field; tablet/Kotlin app insertion is native.
- Show transcript before/while insertion and retain an immediate undo.
- Keep Voice In as the Chrome fallback until this path passes real use.

### Phase 2 — JustShoutIt semantic entry

- Associate each utterance with active item/capture-session context.
- Convert transcript to typed attribute candidates; make uncertainty visible.
- Write only through the manual attribute interface defined by PP-INTAKE-004.
- Persist transcript, parser/model guess, operator-approved value, correction, item/session reference, engine/version, and timestamp.  Store raw audio only under an explicit retention/privacy decision.
- Feed post-photo voice changes into the established `ai_hint`/`ai_reidentify` behavior described in PP-INTAKE-004.

### Phase 3 — responsive Tigwa/Leotha conversation

- Reuse the same capture and playback adapters, with a distinct `converse` request mode and named session scope.
- Use push-to-talk first.  Support interruption: PTT cancels playback and begins a new turn.
- Offer local Android/desktop TTS for short alarm/status fallback; use Edge TTS for normal Tigwa/Leotha response voice.
- Keep visual transcript and local status available if audio playback fails.

### Phase 4 — wake words, only after the above is reliable

Preserve the selected phrase semantics exactly:

```text
Tigwa        → independent normal wake name
Leotha       → independent normal wake name
Hey Tigwa    → independent higher-confidence form
Hey Leotha   → independent higher-confidence form
```

A wake detector only opens a short capture window.  It must not grant execution authority or bypass confirmation.  For Android, that detector belongs in a visible native microphone foreground service (recommended FOSS evaluation: `sherpa-onnx` KWS/VAD/ASR), not a Tasker/Termux-only background listener.  Tasker remains the PTT/diagnostic/relay fallback.  Evaluate false accepts/rejects in the real photo booth before enabling always-listening operation.  The first deployment may appropriately remain PTT-only.

## Security, audit, and failure boundaries

- LAN only, authenticated per device, request expiry/nonce/idempotency, and no arbitrary shell/Tasker/intent execution.
- Audio source identity and active-item association are explicit; never infer an item from broad ambient context.
- No automatic mutation from raw transcript.  Voice commands map to a typed allow-list; material actions require confirmation.
- Capture adapters expose health: mic available, last successful STT, queue age, selected engine, and fallback status.
- Network loss falls back to local STT where selected; if neither route is available, show a clear unavailable state rather than silently dropping speech.
- Preserve transcript/guess/correction provenance as required by PP-INTAKE-004.  Redact secrets and never place credentials in the dataset or documents.

## Explicit non-decisions

- No flake/Nix changes were made or proposed here.
- No Android Tasker profile, microphone configuration, gateway, wake-word listener, or source code was changed.
- The remote photo-booth mic routing and the exact second Linux host are unknown and must be inventoried before implementation.
- The Kotlin intake app is the planned JustShoutIt write surface; this report does not conflate it with earlier Flutter designs.

## Primary sources

- TGW PP-INTAKE-004 — canonical JustShoutIt/write-surface and training-data requirements.
- TGW todo #1353 — verified Hermes Groq STT and Edge TTS status.
- Tasker `Get Voice`: https://tasker.joaoapps.com/userguide/en/help/ah_get_voice.html
- Tasker `Say`: https://tasker.joaoapps.com/userguide/en/help/ah_say.html
- VOSK4Tasker (archived): https://github.com/Admicos/VOSK4Tasker
- Vosk Android: https://alphacephei.com/vosk/android
- Vosk server: https://github.com/alphacep/vosk-server
- whisper.cpp: https://github.com/ggml-org/whisper.cpp
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- nerd-dictation (Vosk-based reference, not selected as platform): https://github.com/ideasman42/nerd-dictation
- Picovoice Porcupine: https://github.com/Picovoice/porcupine
- sherpa-onnx Android / KWS: https://k2-fsa.github.io/sherpa/onnx/android/index.html ; https://k2-fsa.github.io/sherpa/onnx/kws/index.html
- AudioRelay Android/Linux microphone transport: https://audiorelay.net/docs/
- Android microphone foreground-service restrictions: https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start
- openWakeWord: https://github.com/dscripka/openWakeWord
