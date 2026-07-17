# Review request — DeepSeek V4 Flash work routing

**Artifact:** `reference/TGW-DeepSeek-V4-Flash-Work-Routing-2026-07-15.md`  
**Tracker:** #1441 / PP-KNOWLEDGE-001

## Purpose

Review the operating guidance for using DeepSeek V4 Flash to reduce agent cost/context burden without allowing model choice to create authority, discard source knowledge, or cause unverified configuration changes.

## Review questions

1. Does the non-thinking/high/max routing match Dave’s intended cost-versus-reasoning posture?
2. Should any task classes be moved between the three bands?
3. Is the explicit prohibition on applying unverified Aider/API configuration appropriate?
4. Approve use of this guidance for future bounded work while any provider/client integration is separately tested and reviewed?

## Evidence

- Official source read: `https://api-docs.deepseek.com/guides/thinking_mode`
- Preserved source input: `inbox/tigwa/Deepseek-v4-flash-aider-config-tweaks.txt`
- Source checksum: `f7b47ff89b77c2ada884e62bb91299d30e464123868827e6af959ef51b9afb1c`
- No API key, provider configuration, Aider configuration, Hermes configuration, or flake was changed.
