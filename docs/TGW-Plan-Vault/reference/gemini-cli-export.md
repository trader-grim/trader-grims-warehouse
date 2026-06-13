# Gemini CLI Configuration Export

This file contains the exported configuration, custom commands, and environment settings for the Gemini CLI, prepared for reference during the Antigravity CLI migration.

## 1. Global Settings
- **Location:** `~/.gemini/settings.json`
- **Content:**
```json
{
  "security": {
    "auth": {
      "selectedType": "oauth-personal"
    }
  }
}
```

## 2. Project Mappings
- **Location:** `~/.gemini/projects.json`
- **Content:**
```json
{
  "projects": {
    "/home/tgw": "tgw",
    "/opt/TGW/src/trader-grims-warehouse": "trader-grims-warehouse",
    "/opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault": "tgw-plan-vault"
  }
}
```

## 3. State Configuration
- **Location:** `~/.gemini/state.json`
- **Content:**
```json
{
  "startupWarningCounts": {
    "home-directory": 2
  },
  "tipsShown": 10,
  "defaultBannerShownCount": {
    "21e52f476d259e1042a8a5288d4aac95d255037d2448247e55c45c5464bab726": 11,
    "459b34f2fae4af42671ad782df5a6aa05565543956a5e5768cdbe60223191b25": 2
  }
}
```

## 4. Trusted Folders Configuration
- **Location:** `~/.gemini/trustedFolders.json`
- **Content:**
```json
{
  "/opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault": "TRUST_FOLDER",
  "/opt/TGW/src/trader-grims-warehouse": "TRUST_FOLDER"
}
```

## 5. Custom Commands, Skills, and Hooks
- **Status:** No user-defined custom commands, skills, or hooks were discovered in `~/.gemini/` or the project root.
- **Reference:** The `gemini skills list` and `gemini hooks list` commands returned no user-defined assets.

## 6. Notes for Antigravity Migration
- Antigravity CLI is installed and configured in `~/.gemini/antigravity-cli/`.
- Verify the migration of any future session history from `~/.gemini/history/`.
- Ensure MCP servers (configured in `~/.gemini/config/mcp_config.json`, currently empty) are re-configured in the new environment if needed.
