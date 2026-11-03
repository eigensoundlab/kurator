"""Check Kurator's installed dependencies for known vulnerabilities (pip-audit).

Kurator itself is not on PyPI, so auditing the environment directly prints a confusing
"kurator ... could not be audited" line. This audits everything else as a pinned list instead.
Local version tags such as torch 2.x+cu130 are stripped, so CUDA builds of PyTorch are checked too.

    python tools/audit_deps.py [--strict]
Exit code: 0 = nothing known, 1 = vulnerabilities found (or the check failed).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile


def main() -> int:
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze", "--exclude", "kurator", "--exclude-editable"],
                            capture_output=True, text=True, check=True).stdout
    pins = [re.sub(r"\+[A-Za-z0-9.]+$", "", ln.strip()) for ln in freeze.splitlines()
            if ln.strip() and "==" in ln and not ln.startswith("-e ")]
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="kurator-audit-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(pins) + "\n")
        args = [sys.executable, "-m", "pip_audit", "-r", path, "--no-deps", "--disable-pip", "--progress-spinner", "off"]
        if "--strict" in sys.argv:
            args.append("--strict")
        # pip-audit warns that --no-deps lists should be hashed; the list comes from this very environment
        r = subprocess.run(args, stderr=subprocess.PIPE, text=True)
        for line in (r.stderr or "").splitlines():
            if "--no-deps is supported" in line or "pip-compile" in line or "fully hash" in line:
                continue
            print(line, file=sys.stderr)
        if r.returncode == 0:
            print(f"Checked {len(pins)} packages.")
        return r.returncode
    finally:
        os.unlink(path)


if __name__ == "__main__":
    sys.exit(main())
