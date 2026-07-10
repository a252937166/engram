"""Syntax-check the frontend's inline <script> blocks with node --check.

    python3 tests/check_frontend_js.py

Extracts every <script> block from frontend/index.html into a temp file and
runs `node --check` on it. Catches parse errors CI would otherwise miss in
the single-file, framework-free frontend.
"""

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def main():
    html = open(os.path.join(ROOT, "frontend", "index.html")).read()
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    if not blocks:
        print("no script blocks found")
        sys.exit(1)
    src = "\n;\n".join(blocks)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(src)
        path = fh.name
    try:
        subprocess.run(["node", "--check", path], check=True)
    finally:
        os.unlink(path)
    print("FRONTEND JS: syntax OK (%d blocks, %d KB)"
          % (len(blocks), len(src) // 1024))


if __name__ == "__main__":
    main()
