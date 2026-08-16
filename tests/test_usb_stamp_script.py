from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/tgw-usb-stamp.sh"


def test_usb_stamp_replaces_existing_bundle_only_after_temp_bundle_verifies():
    source = SCRIPT.read_text(encoding="utf-8")
    create = 'git -C "$REPO" bundle create - --all > "$BUNDLE_TMP"'
    verify = 'git -C "$REPO" bundle verify "$BUNDLE_TMP"'
    replace = 'mv -f -- "$BUNDLE_TMP" "$MOUNT_DIR/flake/tgw.bundle"'

    assert 'bundle create "$MOUNT_DIR/flake/tgw.bundle"' not in source
    assert 'mktemp "$MOUNT_DIR/flake/.tgw.bundle.tmp.XXXXXX"' in source
    assert source.index(create) < source.index(verify) < source.index(replace)
    assert 'rm -f -- "$BUNDLE_TMP"' in source
    assert 'BUNDLE_TMP=""' in source[source.index(replace) :]
