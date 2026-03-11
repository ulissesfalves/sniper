from __future__ import annotations

import subprocess
import sys
import unittest


class PathSetupBootstrapTest(unittest.TestCase):
    def test_path_setup_preserves_real_polars_when_available(self) -> None:
        script = """
import sys
import tests._path_setup  # noqa: F401
mod = sys.modules.get('polars')
print('POLARS_FILE=', getattr(mod, '__file__', None))
print('HAS_WITH_COLUMNS=', hasattr(getattr(mod, 'DataFrame', object), 'with_columns'))
frame = mod.DataFrame({'x': [1]})
print('FRAME_COLUMNS=', getattr(frame, 'columns', None))
print('FRAME_HAS_WITH_COLUMNS=', hasattr(frame, 'with_columns'))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("POLARS_FILE= /usr/local/lib/python3.11/site-packages/polars/__init__.py", result.stdout)
        self.assertIn("HAS_WITH_COLUMNS= True", result.stdout)
        self.assertIn("FRAME_COLUMNS= ['x']", result.stdout)
        self.assertIn("FRAME_HAS_WITH_COLUMNS= True", result.stdout)


if __name__ == "__main__":
    unittest.main()
