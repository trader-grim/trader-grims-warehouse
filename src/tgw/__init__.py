"""
Trader Grim's Warehouse — inventory management platform.

Core modules:
    tgw.config    Config loading and canonical path resolution
    tgw.resolver  resolve() — maps any identifier to a set of SKUs
    tgw.items     Item read/write operations (the write fence)
    tgw.catalog   Catalog build operations
    tgw.api       CLI entry point
    tgw.logging   Centralized logging setup
    tgw.notify    Notification interface (desktop, file, webhook)
    tgw.health    Platform health checks
    tgw.queue     Queue launcher and state machine
    tgw.workers   Queue worker implementations
    tgw.apis      External API integrations (eBay, future marketplaces)
    tgw.ebay      eBay-specific integration (planned)
"""

__version__ = "0.1.0"
