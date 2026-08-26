"""Opening a URL by handing it to the OS, which ``webbrowser`` can do while
stealing focus from the terminal."""

from __future__ import annotations

import shutil
import subprocess

OPENERS = ("open", "xdg-open")


def open_url(url: str) -> bool:
    for opener in OPENERS:
        if shutil.which(opener):
            subprocess.Popen(
                [opener, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
    return False
