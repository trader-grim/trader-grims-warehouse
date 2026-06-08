# TGW App

Trader Grim's Warehouse Mobile Client (Phase B + C).

## Features

- **Online/Offline Mode**: Seamlessly switches between `tgw-http` API and local `tgwcatalog.db`.
- **Browse**: Infinite scroll grid with search, location, and status filters.
- **Item Detail**: Detailed view of item fields, eBay drafts, and offers.
- **Home**: Dashboard showing connection status and queue counts.
- **Settings**: Configuration for API URL, Auth token, and Offline DB/Thumbnail paths.

## Setup

1. **API Token**: Obtain a Bearer token from the TGW administrator.
2. **Offline DB**: Sync `tgwcatalog.db` and the `thumbnails` folder to your device (e.g., via Syncthing).
3. **Configure**: Open the **Settings** tab in the app and set:
   - Base URL (e.g., `http://192.168.1.100:7373`)
   - Bearer Token
   - Catalog DB Path
   - Thumbnail Directory

## Running on Linux

Ensure you have the following system dependencies (Ubuntu/Debian):
```bash
sudo apt-get install libsecret-1-dev libjsoncpp-dev libsecret-1-0
```

Run the app:
```bash
flutter run -d linux
```

## Building for Android

Ensure Android SDK is configured and licenses are accepted.

```bash
flutter build apk --release
```

## Architecture

- **State Management**: Riverpod
- **HTTP Client**: Dio
- **Database**: sqflite (with ffi for Linux)
- **Storage**: flutter_secure_storage
