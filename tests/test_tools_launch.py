import unittest
from unittest.mock import patch

from tools import shared


class FakeBlocks:
    def __init__(self):
        self.launch_kwargs = None

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return "launched"


class ToolLaunchTests(unittest.TestCase):
    def test_port_auto_uses_random_available_port(self):
        with patch("tools.shared._pick_random_port", return_value=54321):
            kwargs = shared.launch_kwargs_from_cli(["--port", "auto"])

        self.assertEqual(kwargs, {"server_port": 54321})

    def test_numeric_port_is_forwarded_to_gradio(self):
        app = FakeBlocks()

        result = shared.launch_blocks(app, css="css", js="js", argv=["--port", "51234"])

        self.assertEqual(result, "launched")
        self.assertEqual(
            app.launch_kwargs,
            {"css": "css", "js": "js", "server_port": 51234},
        )

    def test_invalid_port_fails_argument_parsing(self):
        with self.assertRaises(SystemExit):
            shared.launch_kwargs_from_cli(["--port", "autoish"])


if __name__ == "__main__":
    unittest.main()
