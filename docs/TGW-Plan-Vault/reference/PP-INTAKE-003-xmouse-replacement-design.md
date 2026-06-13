# Survey & Design: TGW-Native XMouse Replacement App (PP-INTAKE-003)
**Plan Reference:** [PP-INTAKE-001 / XMouse Replacement](file:///opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault/suggestions/SUGGESTIONS.md#L178)  
**Task ID:** todo #116  
**Target Platform:** Android (Android 10+ Tablet)  
**Status:** Survey & Architecture Proposal (Deliver to `inbox/`)  

---

## 1. Executive Summary

The warehouse operator currently uses a tablet running a macro-pad app (referred to as `xmouse`) to dispatch SSH commands (like `tgw set-template`) to the master station. In addition, the operator needs a VNC/RDP viewer to interact with the master station's desktop and a web browser to fill out item forms.

This document surveys the open-source candidate repositories for remote viewers, macro pads, and SSH libraries. It evaluates two implementation paths:
1. **Native Android (Java/Kotlin + NDK)** using copyleft GPLv3 libraries.
2. **Flutter (Dart + Platform Channels)** using permissive MIT/Apache-2.0 libraries.

We recommend the **Flutter-based architecture** due to its cross-platform simplicity, permissive licensing, and alignment with the existing TGW Flutter client codebase (**GEMINI-003**).

---

## 2. Candidate Repositories & License Posture

### 2.1 Remote VNC/RDP Viewer Stack
*   **[iiordanov/remote-desktop-clients](https://github.com/iiordanov/remote-desktop-clients) (Native Android)**
    *   **Description:** The source codebase for `aRDP` and `bVNC` (highly optimized Android VNC/RDP viewers utilizing FreeRDP and libvncclient).
    *   **License:** **GPLv3** (with minor MPL/LGPL exceptions for keyboard/spice components).
    *   **Posture:** Strong copyleft. Linking or integrating this code forces the entire TGW app to be open-sourced under the GPLv3.
*   **[flutter_rfb](https://pub.dev/packages/flutter_rfb) (Flutter/Dart)**
    *   **Description:** A pure Dart implementation of the RFB (VNC) protocol (RFC 6143).
    *   **License:** **Apache-2.0**
    *   **Posture:** Permissive. Allows integration without copyleft contamination.
*   **[RustDesk](https://github.com/rustdesk/rustdesk) (Rust + Flutter)**
    *   **Description:** Open-source remote desktop client.
    *   **License:** **GPLv3**
    *   **Posture:** Copyleft. Rust core + Flutter UI. Extremely complex to fork and strip down for simple local network VNC.

### 2.2 SSH Command Dispatch Stack
*   **[ConnectBot](https://github.com/connectbot/connectbot) (Native Android)**
    *   **Description:** The premier open-source Android SSH client.
    *   **License:** **Apache-2.0**
*   **[dartssh2](https://pub.dev/packages/dartssh2) (Flutter/Dart)**
    *   **Description:** A pure Dart SSH client library.
    *   **License:** **MIT**
    *   **Posture:** Permissive. Lightweight and easily integrated directly into Flutter state managers.

---

## 3. Architecture Comparison: Native Android vs. Flutter

| Criteria | Native Android (Kotlin + NDK) | Flutter (Dart + Packages) |
|----------|-------------------------------|---------------------------|
| **Core Viewers** | `iiordanov/remote-desktop-clients` (aRDP/bVNC) | `flutter_rfb` (VNC client) |
| **Command Dispatch** | JSch (SSH) / OkHttp (HTTP) | `dartssh2` (SSH) / `dio` (HTTP) |
| **Form Surface** | Android System WebView | `flutter_inappwebview` |
| **Licensing** | **GPLv3** (Copyleft restriction) | **Apache-2.0 / MIT** (Permissive) |
| **NDK Dependency** | Yes (FreeRDP/libvncclient C compiles) | No (Pure Dart/Flutter engine wrapper) |
| **Build Size** | Large (~30MB+ due to multi-arch .so files) | Medium (~15MB) |
| **Code Reuse** | Zero (Must write Kotlin/Java) | High (Can share modules with `tgw_app`) |

### Development Decision
We recommend **Flutter** for the combined app. Building a VNC client via `flutter_rfb` avoids having to compile Native C/C++ libraries (FreeRDP/libvncclient) using Android NDK toolchains, which is notoriously difficult to maintain. A pure-Dart implementation of SSH (`dartssh2`) and VNC (`flutter_rfb`) ensures compile-once-run-anywhere performance.

---

## 4. Proposed Combined-App Architecture (Flutter)

The combined app interface is designed as a **unified dashboard panel** optimized for tablet layouts.

### 4.1 UI Layout Proposal (Split Screen)
```
+-----------------------------------------------------------------------+
|  TGW XMouse Console (ONLINE - 192.168.1.50)                           |
+------------------------------------+----------------------------------+
|  Left Side: Macro-Pad Grid         |  Right Side: Active Viewer/Form  |
|  [Books]      [Power Supply]       |  +----------------------------+  |
|  [Magazines]  [Kitchen]            |  |                            |  |
|  [Manuals]    [Sewing]             |  |  Embedded VNC Viewer       |  |
|  [Stamps]     [Mugs]               |  |  (Master Desktop Stream)   |  |
|  [Cassettes]  [Records]            |  |                            |  |
|                                    |  +----------------------------+  |
|  --------------------------------  |  |  HTTP Web Form Panel       |  |
|  Override Action Panel             |  |  (tgw-http /form/<sku>)    |  |
|  [Weight (oz)]  [Override Cond]    |  |                            |  |
|  [Sync Catalog] [Sweep Check]      |  +----------------------------+  |
+------------------------------------+----------------------------------+
```

### 4.2 Data & Control Flow
1.  **Macro Button Tap:** The operator taps the `Books` button.
2.  **State Manager Action:** Riverpod triggers a command dispatch.
3.  **Command Path:**
    - *Path A (API - default):* Sends `POST /api/items/{sku}/set-template?group_key=books` via `Dio`.
    - *Path B (SSH - fallback/legacy):* Connects via `dartssh2` client and executes: `tgw set-template books <sku>`.
4.  **UI Feedback:**
    - The VNC Viewer pane shows the master desktop's terminal updating.
    - Web Form Panel reloads the new template-applied fields from `tgw-http/form/<sku>`.

---

## 5. Implementation Roadmap

*   **Phase 1: Macro Pad Grid + SSH/HTTP Dispatch**  
    Build the UI GridView layout. Wire up standard `Dio` HTTP calls and fallback SSH triggers using `dartssh2`.
*   **Phase 2: Form Tool Integration**  
    Integrate `flutter_inappwebview` to render the FastAPI `/form/*` web pages inline.
*   **Phase 3: Embedded VNC Viewer**  
    Implement VNC rendering using `flutter_rfb` directly inside the right pane, connected to the VNC server running on the TGW master station.
