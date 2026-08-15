#!/usr/bin/env python3
#
# PP-KNOWLEDGE-001 Track R3 (todo #1518) — tesseract-OCR-via-recoll-filter
# proof-of-mechanism, built to make serials/labels/barcodes visible in
# ItemData photos searchable through `tgw search --full-text` /
# `tgw_search_full` (the surfaces Track R2 / todo #1147 built).
#
# WHY THIS FILE EXISTS (not just `mimeconf: image/jpeg = execm rclocr.py`):
# Recoll ships a bundled OCR helper, rclocrtesseract.py (ocrpossible() /
# runocr()), but it is a one-shot helper meant to be CALLED BY another
# persistent-protocol filter (e.g. rclpdf.py falls back to it when a PDF
# has no extractable text). It does not itself speak recoll's rclexecm
# "execm" wire protocol, so pointing an image mimetype straight at it in
# mimeconf fails silently at index time ("rclocr: Usage: rclocr.py
# <imagefilename>" / "MHExecMultiple: getline error" in the recollindex
# log; zero documents actually get OCR text, no query ever fails loudly).
# Verified live 2026-07-18, see docs/TGW-Plan-Vault/plan/packets/results/
# 1518-RESULT.md.
#
# This script is the missing piece: an rclimg.py-shaped persistent-protocol
# handler (RclBaseHandler subclass, same shape as recoll's own bundled
# EXIF-tag filter share/recoll/filters/rclimg.py) that calls straight into
# rclocrtesseract's runocr() instead of pyexiv2, and emits the OCR text as
# a minimal HTML document (recoll's own convention for extracted text).
#
# A second live bug fixed here, also verified 2026-07-18: recoll's execm
# wire protocol hands the filename to html_text() as BYTES. The bundled
# rclocrtesseract module's ocrpossible() does a str-only
# `os.path.splitext(path)[1].lower() in _okexts` check (_okexts is a tuple
# of str) — a bytes path silently fails that comparison and ocrpossible()
# returns False for every real image, again with no visible error. Decode
# to str before calling into rclocrtesseract.
#
# Deployment (NOT done by this packet — see 1518-RESULT.md "what the
# full-fleet sweep would need"):
#   1. Copy/reference this file from the live recoll config, e.g.
#      /opt/TGW/.recoll/rclimg_ocr.py (or leave in the repo checkout and
#      point at it directly — either works, execm takes an absolute path).
#   2. In /opt/TGW/.recoll/mimeconf, under [index]:
#        image/jpeg = execm /opt/TGW/.recoll/rclimg_ocr.py
#        image/png  = execm /opt/TGW/.recoll/rclimg_ocr.py
#        image/tiff = execm /opt/TGW/.recoll/rclimg_ocr.py
#      (this REPLACES the default `execm rclimg` EXIF-tag mapping for
#      these mimetypes — rclimg.py's EXIF/XMP tag extraction is not run
#      when this filter is active; if both are wanted, this file would
#      need extending to call both and concatenate, not attempted here)
#   3. In /opt/TGW/.recoll/recoll.conf:
#        ocrprogs = tesseract
#        tesseractcmd = <absolute path to a tesseract binary — nothing is
#          on PATH for the tgw user today, see 1518-RESULT.md; tesseract
#          is available in nixpkgs (tesseract-5.5.0) but is NOT installed
#          system-wide by any flake in this repo or /home/db/tgw-flake as
#          of 2026-07-18 — that's a separate follow-on todo, filed>
#        tesseractlang = eng
#   4. Add the desired photo path(s) to topdirs (or index a bounded batch
#      with `recollindex -i <files...>`, avoiding a topdirs change and the
#      full-tree walk it triggers). Full ItemData is explicitly NOT to be
#      added as a topdir by this packet — see 1518-RESULT.md's thermal/
#      scope notes.
#
# Reuses recoll's own bundled filter helpers (rclexecm, rclbasehandler,
# rclocrtesseract, rclconfig) rather than vendoring them — resolved at
# runtime via the `recollindex` binary's install location so this file
# does not hardcode a Nix store path that will go stale on a recoll
# version bump.

import os
import shutil
import sys


def _recoll_filters_dir() -> str:
    """Locate recoll's bundled share/recoll/filters directory relative to
    the recollindex binary actually on PATH, so this file has no hardcoded
    Nix store path to go stale on a recoll upgrade."""
    exe = shutil.which("recollindex")
    if not exe:
        raise RuntimeError("recollindex not found on PATH")
    real = os.path.realpath(exe)
    # .../bin/recollindex -> .../share/recoll/filters
    prefix = os.path.dirname(os.path.dirname(real))
    return os.path.join(prefix, "share", "recoll", "filters")


sys.path.insert(0, _recoll_filters_dir())

import rclconfig  # noqa: E402
import rclexecm  # noqa: E402
import rclocrtesseract  # noqa: E402
from rclbasehandler import RclBaseHandler  # noqa: E402


class OcrImgExtractor(RclBaseHandler):
    def __init__(self, em):
        super(OcrImgExtractor, self).__init__(em)
        self.config = rclconfig.RclConfig()

    def html_text(self, filename):
        # See module docstring: rclexecm hands us bytes; rclocrtesseract's
        # ocrpossible()/runocr() do str-only path/ext comparisons, so a
        # bytes path silently makes ocrpossible() return False.
        if isinstance(filename, bytes):
            filename = filename.decode("utf-8", errors="replace")
        self.config.setKeyDir(os.path.dirname(filename))
        if not rclocrtesseract.ocrpossible(self.config, filename):
            return b"<html><head></head><body></body></html>"
        ok, data = rclocrtesseract.runocr(self.config, filename)
        if not ok:
            return b"<html><head></head><body></body></html>"
        if isinstance(data, str):
            data = data.encode("utf-8", errors="replace")
        escaped = (
            data.replace(b"&", b"&amp;").replace(b"<", b"&lt;").replace(b">", b"&gt;")
        )
        return b"<html><head></head><body><pre>" + escaped + b"</pre></body></html>"


if __name__ == "__main__":
    proto = rclexecm.RclExecM()
    extract = OcrImgExtractor(proto)
    rclexecm.main(proto, extract)
