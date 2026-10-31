"""
vigil_open.py — what clicking a Vigil notification runs (gil-vigil:open).

Brings the Vigil window forward, or asks whichever process hosts Vigil (GIL,
or the watcher while GIL is closed) to open it. If nothing hosts Vigil — an
old notification clicked after everything closed — it starts GIL, which
opens Vigil once it's up. The link's argument is ignored: any page could
send one, so all it can ever do is this.
"""

import os
import subprocess
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)


def main() -> None:
    import vigil_chat
    if vigil_chat.open_from_outside():
        return
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    subprocess.Popen([pyw if os.path.exists(pyw) else sys.executable,
                      os.path.join(_DIR, "main.py"), "--open-vigil"],
                     cwd=_DIR, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


if __name__ == "__main__":
    main()
