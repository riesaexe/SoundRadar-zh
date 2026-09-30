"""Register the bundled Qt and Shiboken DLL directories on Windows."""

import os
import sys


if sys.platform == "win32":
    _dll_directory_handles = [
        os.add_dll_directory(os.path.join(sys._MEIPASS, package))
        for package in ("PySide6", "shiboken6")
    ]
