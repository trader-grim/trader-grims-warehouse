# Trader Grim's Warehouse

Inventory management platform for a multi-marketplace used goods business.
Running since 2011.

## Overview

TGW is a filesystem-first inventory platform.  Per-item JSON records are the
canonical data store.  All reads and writes go through `tgw-api`.  The queue
system coordinates processing workers.  eBay is the primary marketplace, with
multi-marketplace support planned.

## Install (development)

```bash
cd /opt/TGW/tgw-lib/src/trader-grims-warehouse
pip install -e ".[dev]"
```

## Usage

```bash
# Resolve a location to all SKUs
tgw resolve --location FF0779

# Move all items from one location to another
tgw catlocmvall FF0779 PB1018

# Update a field on one item
tgw update tgw202604141102250 title "Vintage Widget"

# Bulk update by selector
tgw update-where --location FF0779 VERIFIED yes

# Build all catalogs
tgw build-all
```

## Project Structure

```
src/tgw/
    api.py          CLI entry point and thin command wrappers
    config.py       Config loading and canonical path resolution
    resolver.py     resolve() — maps any identifier to a set of SKUs
    catalog.py      Catalog build operations
    queue/          Queue launcher, state machine, schema
    workers/        Queue worker implementations
tests/              pytest test suite
docs/               Project planning documents
config/             Reference config (production lives at /opt/TGW/config)
bin/                Shell entry points
```

## Architecture

- **tgw-api is the fence.**  Nothing touches ItemData directly except through
  tgw-api functions.  The JSON backend is an implementation detail.
- **resolve() is the selector engine.**  Give it any identifier (SKU, location,
  UPC, eBay item ID, date range, free text) and get back a set of SKUs.
- **Bulk operations are first-class.**  Workers claim all available jobs at
  once and use `update_items()` / `update_where()` rather than sequential
  one-item loops.
- **Queue workers are thin.**  They ask tgw-api; they don't reason about paths.
- **Output contract.**  Every CLI call returns exactly one JSON object:
  `{"ok": true, ...}` or `{"ok": false, "error": "..."}`.

## Configuration

Default config: `/opt/TGW/config/tgw-api-config.json`
Override: `tgw --config /path/to/config.json <subcommand>`

## Requirements

- Python 3.11+
- `/opt/TGW/` filesystem layout (see `docs/tgw-master-map.md`)
- Ollama (optional, for AI-assisted listing workflows)
- PostgreSQL (optional, for distributed queue state machine)
