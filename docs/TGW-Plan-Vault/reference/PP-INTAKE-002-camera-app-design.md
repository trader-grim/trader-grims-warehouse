# Design Doc & Flutter Scaffold Proposal: TGW-Native Camera App (PP-INTAKE-002)
**Plan Reference:** [PP-INTAKE-001 / Camera App](file:///opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault/suggestions/SUGGESTIONS.md#L177)  
**Task ID:** todo #115  
**Target Platform:** Android (Android 10+ Tablet / Phone)  
**Status:** Design Proposal (Deliver to `inbox/`)  

---

## 1. Executive Summary & Problem Statement

Currently, the TGW warehouse photography workflow relies on a fragile combination of **Tasker automation**, **the stock Android camera app**, and the **official Foldio360 turntable app**. 

This setup has two major bottlenecks:
1. **Interface Fragmentation:** The operator must switch between scanning barcodes, selecting templates, and launching the camera.
2. **Foldio360 Zip Delay:** The official Foldio360 app does not expose captured photos until it compiles them into a ZIP file at the end of a turntable rotation. Compiling this ZIP file takes 15–30 seconds per item, which doubles the operator's queue processing time.

This proposal describes a single, unified **TGW-Native Camera App** built in Flutter (following the **GEMINI-003** pattern). It incorporates barcode scanning, template selection via a Heads-Up Display (HUD), voice feedback, dual upload options, and a **root-level zip-bypass** to extract raw JPEGs from the Foldio360 cache in real-time.

---

## 2. Architecture & The GEMINI-003 Pattern

The app follows the established **GEMINI-003** Flutter architecture, using:
- **State Management:** Riverpod (`flutter_riverpod`) for reactive providers.
- **Networking:** `Dio` client for direct HTTP API communication with `tgw-http` (port 7373).
- **Offline Mode:** `sqflite` for reading `tgwcatalog.db` synced locally via Syncthing.
- **Repository Pattern:** A unified repository wrapper that decides between local cache vs API depending on connection status.

```
+------------------------------------------------------------+
|                     TGW Camera App UI                      |
|  +---------------------+        +-----------------------+  |
|  |     Camera HUD      |        |     Barcode Scan      |  |
|  +---------------------+        +-----------------------+  |
+------------------------------------------------------------+
                              |
                              v
+------------------------------------------------------------+
|                   Riverpod State Providers                 |
|  - SkuProvider        - TemplateProvider  - UploadQueue    |
+------------------------------------------------------------+
                              |
                              v
+------------------------------------------------------------+
|                       Services Layer                       |
|  +------------------+  +-----------------+  +------------+  |
|  |   RootService    |  |  UploadService  |  | TTSService |  |
|  | (Foldio360 Poll) |  | (POST/SyncFolder|  | (Voice)    |  |
|  +------------------+  +-----------------+  +------------+  |
+------------------------------------------------------------+
```

---

## 3. Core Features Spec

### 3.1 Barcode Scanning
- **Libraries:** `mobile_scanner` (based on Google ML Kit).
- **Behavior:** The scanner runs inside the camera preview layer. When a SKU barcode (format: `tgwYYYYMMDD...`) is detected:
  1. The app sets the active SKU state.
  2. The app triggers a short confirmation chime.
  3. The TTS engine announces the SKU: *"SKU set to tgw two zero two six..."*

### 3.2 Template Select & SETTEMPLATE HUD
- **Behavior:** A floating overlay HUD displays the 20 category templates.
- **Tap Actions:** Selecting a template performs three actions:
  1. Updates the active template state.
  2. Copies `SETTEMPLATE:<Category Name>` to the system clipboard (which KDE Connect relays to the clipboard on the master station).
  3. Pings `POST /api/items/{sku}/set-template` to write the template group defaults directly to the server database.
  4. Triggers voice confirmation: *"Books template selected."*

### 3.3 Voice Hint (Text-to-Speech)
- **Library:** `flutter_tts`
- **Purpose:** Allows the operator to work eyes-free without looking at the tablet screen.
- **Announcements:**
  - *"SKU set."*
  - *"Template: [Name]"*
  - *"Turntable starting."*
  - *"Upload complete for [SKU]."*

### 3.4 Dual Upload Options
1. **Local Syncthing Folder (Offline-friendly):**
   - Captured photos are written to a designated local directory: `/sdcard/Pictures/TGW_Sync/<SKU>/<SKU>_01.jpg`.
   - The background Syncthing client automatically syncs this folder to the master server.
2. **tgw-http POST (Online-priority):**
   - Raw bytes are sent via a multi-part HTTP POST: `POST /api/items/{sku}/photos` with Bearer Auth.

---

## 4. Foldio360 Integration & Zip-Bypass

### 4.1 Short-Term: Root-Access Polling (Bypass)
Since the official Foldio360 app runs on the same Android device, we can bypass its ZIP compile step if the device is **rooted** (using standard Magisk/KernelSU).

- **Cache Directory:** During a rotation, Foldio360 stores temporary, unzipped JPEGs in its private app folder: `/data/data/com.orangemonkie.foldio360/cache/` or `/data/user/0/com.orangemonkie.foldio360/files/temp/`.
- **Extraction Logic:** 
  1. The TGW app spawns a root shell (`su`).
  2. A file observer (using `inotifywait` via shell or directory polling) watches the cache folder.
  3. As soon as a photo is written (e.g., `temp_01.jpg`), our service copies it to our Syncthing folder or upload queue.
  4. Once all 24 or 36 shots are copied, the app triggers a TTS notice: *"Capture complete. You can cancel the zip process."*

### 4.2 Long-Term: Direct BLE Control & Custom ROMs
To eliminate the official app completely, the TGW app can control the turntable directly via Bluetooth Low Energy (BLE).

- **Library:** `flutter_blue_plus`
- **Protocol Sniffing (Reverse-Engineered GATT):**
  - Connect to the turntable advertised as `Foldio360`.
  - Write commands to the control GATT characteristic (typically 1-byte control strings for speed, rotation degrees, and LED halo brightness).
  - **Sequence:** Send rotate 10 degrees command $\rightarrow$ wait $\rightarrow$ trigger Flutter Camera API capture $\rightarrow$ repeat.
- **Custom-ROM Path:** For legacy or dedicated camera hardware, deploying custom ROMs (e.g., LineageOS with built-in su privileges and default BLE pairings) ensures plug-and-play behavior across devices without manual rooting.

---

## 5. Flutter Scaffold File Tree Proposal

```
apps/tgw_camera_app/
├── android/
├── pubspec.yaml                 # Dependencies: mobile_scanner, flutter_riverpod, dio, flutter_tts, flutter_blue_plus
└── lib/
    ├── main.dart
    ├── app.dart
    ├── config/
    │   └── app_config.dart
    ├── services/
    │   ├── tts_service.dart      # FlutterTTS wrapper
    │   ├── upload_service.dart   # Dio POST / local copy
    │   ├── root_service.dart     # Executes `su` commands for Foldio360 bypass
    │   └── ble_service.dart      # BLE direct turntable controller
    └── features/
        ├── active_state/
        │   └── state_providers.dart # Riverpod active SKU / Template state
        └── camera/
            ├── camera_screen.dart
            ├── widgets/
            │   ├── barcode_overlay.dart
            │   └── template_hud.dart
            └── controllers/
                └── camera_controller.dart
```

---

## 6. Scaffold Code Implementation Proposals

### 6.1 State Management (`state_providers.dart`)
```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';

class SkuState extends StateNotifier<String?> {
  SkuState() : super(null);
  void setSku(String sku) => state = sku;
  void clear() => state = null;
}

final activeSkuProvider = StateNotifierProvider<SkuState, String?>((ref) => SkuState());

final activeTemplateProvider = StateProvider<String?>((ref) => null);

enum ConnectionStatus { online, offline }
final connectionStatusProvider = StateProvider<ConnectionStatus>((ref) => ConnectionStatus.online);
```

### 6.2 Root Zip-Bypass Service (`root_service.dart`)
```dart
import 'dart:io';
import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';

class FoldioBypassService {
  final Ref ref;
  Timer? _pollTimer;
  final String foldioCachePath = "/data/data/com.orangemonkie.foldio360/cache/";
  final String syncDestination = "/sdcard/Pictures/TGW_Sync/";

  FoldioBypassService(this.ref);

  Future<bool> checkRootAccess() async {
    try {
      final result = await Process.run('su', ['-c', 'id']);
      return result.exitCode == 0 && result.stdout.toString().contains("uid=0");
    } catch (e) {
      return false;
    }
  }

  void startPolling(String sku) {
    _pollTimer?.cancel();
    _pollTimer = Timer.periodic(Duration(milliseconds: 500), (timer) async {
      final rootAvailable = await checkRootAccess();
      if (!rootAvailable) return;

      // Execute 'su' command to list files in foldio cache
      final lsResult = await Process.run('su', ['-c', 'ls $foldioCachePath']);
      if (lsResult.exitCode != 0) return;

      final files = lsResult.stdout.toString().split('\n').where((f) => f.endsWith('.jpg'));
      for (var file in files) {
        final destFolder = "$syncDestination$sku/";
        await Process.run('su', ['-c', 'mkdir -p $destFolder && cp $foldioCachePath$file $destFolder$file']);
        // Delete original cache file to prevent duplicate processing
        await Process.run('su', ['-c', 'rm $foldioCachePath$file']);
        
        // Notify state / trigger TTS
        ref.read(ttsProvider).speak("Photo captured");
      }
    });
  }

  void stopPolling() {
    _pollTimer?.cancel();
  }
}

final foldioBypassProvider = Provider((ref) => FoldioBypassService(ref));
final ttsProvider = Provider((ref) => TTSService()); // Stub for TTS
```

### 6.3 BLE Direct Controller Service (`ble_service.dart`)
```dart
import 'package:flutter_blue_plus/flutter_blue_plus.dart';

class BLETurntableService {
  BluetoothDevice? _device;
  BluetoothCharacteristic? _writeCharacteristic;

  Future<void> connectToTurntable() async {
    FlutterBluePlus.startScan(timeout: Duration(seconds: 4));
    
    FlutterBluePlus.scanResults.listen((results) async {
      for (ScanResult r in results) {
        if (r.device.platformName == "Foldio360") {
          _device = r.device;
          await FlutterBluePlus.stopScan();
          await _device!.connect();
          
          List<BluetoothService> services = await _device!.discoverServices();
          for (BluetoothService service in services) {
            for (BluetoothCharacteristic characteristic in service.characteristics) {
              if (characteristic.properties.write) {
                _writeCharacteristic = characteristic;
              }
            }
          }
        }
      }
    });
  }

  Future<void> triggerStep(int degrees) async {
    if (_writeCharacteristic == null) return;
    // Example: BLE command protocol payload mapping for rotation
    final commandBytes = [0x01, degrees]; 
    await _writeCharacteristic!.write(commandBytes, withoutResponse: false);
  }
}
```

---

## 7. Review and Feedback request

Please review the architectural details before we implement this scaffold:
1. **Root Privilege Strategy:** Confirm if we should package `su` polling inside our app or bundle it as a separate background shell script.
2. **Device Hardware Support:** Specify target devices for easy root access (e.g. Google Pixel or Xiaomi) to structure our custom ROM documentation.
3. **Syncthing Path Preferences:** Ensure the default directory `/sdcard/Pictures/TGW_Sync/` is aligned with the Syncthing folder mappings.
