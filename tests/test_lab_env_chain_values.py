from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "shared" / "scripts" / "upsert-lab-env.py"


class LabEnvChainValueTests(unittest.TestCase):
    def test_accepts_public_hoodi_chain_identity_values(self) -> None:
        values = {
            "PUBKEY": "0x" + "11" * 48,
            "DEPOSIT_TX": "0x" + "22" * 32,
            "HOODI_DEPOSIT_CONTRACT": "0x" + "33" * 20,
            "VALIDATOR_INDEX": "12345",
        }
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "lab.env"
            target.write_text("", encoding="utf-8")
            for name, value in values.items():
                result = subprocess.run(
                    ["python3", str(HELPER), "--file", str(target), name, value],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            body = target.read_text(encoding="utf-8")
        for name, value in values.items():
            self.assertIn(f"export {name}={value}", body)

    def test_rejects_malformed_deposit_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "lab.env"
            result = subprocess.run(
                [
                    "python3",
                    str(HELPER),
                    "--file",
                    str(target),
                    "HOODI_DEPOSIT_CONTRACT",
                    "0x1234",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid_HOODI_DEPOSIT_CONTRACT", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
