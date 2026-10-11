import contextlib
import io
import unittest
from unittest import mock

from linear_sidebar import cli


class OptionsTest(unittest.TestCase):
    def test_port_and_refresh_reach_the_server(self):
        with mock.patch.object(cli.server, "main", return_value=0) as server_main:
            self.assertEqual(cli.main(["--port", "47802", "--refresh-seconds", "30"]), 0)
        server_main.assert_called_once_with(port=47802, refresh_seconds=30)

    def test_without_options_the_environment_decides(self):
        with mock.patch.object(cli.server, "main", return_value=0) as server_main:
            cli.main([])
        server_main.assert_called_once_with(port=None, refresh_seconds=None)

    def test_bad_values_are_refused(self):
        for argv in (["--port", "0"], ["--port", "70000"], ["--port", "web"], ["--refresh-seconds", "-5"]):
            with self.subTest(argv=argv):
                with mock.patch.object(cli.server, "main") as server_main, \
                        contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    cli.main(argv)
                server_main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
