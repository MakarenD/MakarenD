from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from profile.generate import DEFAULT_CONFIG, load_config
from profile.github_data import ContributionDataError


class ConfigTests(unittest.TestCase):
    def test_default_config_contract(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        self.assertEqual("MakarenD", config["identity"]["github_username"])
        self.assertEqual(
            ["backend", "frontend", "data", "platform"], list(config["capabilities"])
        )
        self.assertEqual(
            ["UNIVER-Project"], [item["name"] for item in config["connected_systems"]]
        )

    def test_connected_system_supports_arbitrary_kind_and_optional_status(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        config["connected_systems"].append(
            {
                "id": "example-service",
                "kind": "service",
                "name": "Example",
                "url": "https://github.com/example",
                "description": "GitHub project",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            loaded = load_config(path)
        self.assertEqual(
            ["UNIVER-Project", "Example"],
            [item["name"] for item in loaded["connected_systems"]],
        )

    def test_invalid_or_insecure_system_is_rejected(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        config["connected_systems"][0]["url"] = "http://github.com/UNIVER-Project"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(ContributionDataError):
                load_config(path)

    def test_duplicate_or_unsafe_system_id_and_site_url_are_rejected(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        config["connected_systems"].append(dict(config["connected_systems"][0]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(ContributionDataError):
                load_config(path)
            config["connected_systems"].pop()
            config["identity"]["site_url"] = "http://makaren.pro"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(ContributionDataError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
