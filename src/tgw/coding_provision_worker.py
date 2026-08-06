"""One-shot local worker for a durable coding provision request."""

from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path

from tgw.coding_provision import claim_and_run
from tgw.config import DEFAULT_CONFIG, load_coding_worker_config


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-coding-provision-worker")
    parser.add_argument("request_id")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--host", default=socket.gethostname())
    parser.add_argument("--worker-identity", required=True)
    args = parser.parse_args()
    result = claim_and_run(
        load_coding_worker_config(Path(args.config)),
        request_id=args.request_id,
        local_host=args.host,
        worker_identity=args.worker_identity,
    )
    print(json.dumps(result.get("receipt"), sort_keys=True))
    return 0
