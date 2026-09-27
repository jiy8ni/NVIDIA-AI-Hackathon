from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "slack_user_oauth", Path(__file__).parents[1] / "scripts" / "slack_user_oauth.py"
)
assert SPEC and SPEC.loader
slack_user_oauth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(slack_user_oauth)


class SlackUserOAuthTest(unittest.TestCase):
    def test_save_env_value_replaces_existing_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("SLACK_USER_TOKEN=old\nNOTION_TOKEN=value\n", encoding="utf-8")

            slack_user_oauth.save_env_value(path, "SLACK_USER_TOKEN", "new")

            self.assertEqual(path.read_text(encoding="utf-8"), "SLACK_USER_TOKEN=new\nNOTION_TOKEN=value\n")


if __name__ == "__main__":
    unittest.main()
