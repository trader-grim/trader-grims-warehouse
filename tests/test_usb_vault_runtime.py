from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tgw-usb-stamp.sh"


class UsbVaultRuntimeContractTests(unittest.TestCase):
    def test_usb_stamp_uses_command_local_safe_directory_for_bundle(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            'git -c safe.directory="$REPO" -C "$REPO" bundle create',
            text,
        )
        self.assertNotIn("git config --global", text)


if __name__ == "__main__":
    unittest.main()
