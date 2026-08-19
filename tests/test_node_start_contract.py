from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NodeStartContractTests(unittest.TestCase):
    def test_primary_client_playbooks_start_their_services(self) -> None:
        cases = (
            ("nethermind.yml", "nethermind.service"),
            ("lighthouse-bn.yml", "lighthouse-beacon.service"),
        )
        for playbook, service in cases:
            text = (
                ROOT / "primary-aws" / "ansible" / "playbooks" / playbook
            ).read_text(encoding="utf-8")
            self.assertIn(service, text, playbook)
            self.assertRegex(text, re.compile(r"state:\s*started"), playbook)

    def test_standby_has_explicit_el_bn_start_playbook(self) -> None:
        path = (
            ROOT
            / "standby-aws"
            / "ansible"
            / "playbooks"
            / "start-el-bn.yml"
        )
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        for service in ("nethermind.service", "lighthouse-beacon.service"):
            self.assertIn(service, text)
        self.assertGreaterEqual(len(re.findall(r"state:\s*started", text)), 2)
        self.assertIn("lighthouse-validator.service", text)
        self.assertRegex(text, re.compile(r"masked|is-enabled", re.IGNORECASE))

    def test_standby_site_starts_el_bn_after_install(self) -> None:
        text = (
            ROOT / "standby-aws" / "ansible" / "playbooks" / "site.yml"
        ).read_text(encoding="utf-8")
        self.assertLess(text.index("install-clients.yml"), text.index("start-el-bn.yml"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
