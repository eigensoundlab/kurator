"""Failed tests, short, for the GitHub Actions run summary (CI's "Failures" step).

    python tools/ci_failures.py pytest-report.xml "test (macos-latest, 3.14)" >> "$GITHUB_STEP_SUMMARY"

Reads pytest's JUnit XML and writes Markdown: the job, the counts, and per failed test its name,
message and the end of its traceback - everything needed to fix it, with none of the passing tests.
Every failing job adds its part, so the run's Summary page holds all failures on one page.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

KEEP_LINES = 45


def main(path: str, job: str) -> str:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        return f"### ❌ {job}\nNo test report ({e}) - the tests did not run; see the log of the Install step.\n"
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    n = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    out = [f"### ❌ {job} - {n['failures'] + n['errors']} failed, "
           f"{n['tests'] - n['failures'] - n['errors'] - n['skipped']} passed, {n['skipped']} skipped", ""]
    for case in root.iter("testcase"):
        for bad in [*case.findall("failure"), *case.findall("error")]:
            mod = case.get("classname", "").replace(".", "/")
            name = f"{mod}.py::{case.get('name')}" if mod else case.get("name", "?")
            kind = "error" if bad.tag == "error" else "failed"
            lines = (bad.text or "").rstrip().splitlines()
            if len(lines) > KEEP_LINES:
                lines = ["…"] + lines[-KEEP_LINES:]
            out += [f"**{name}** ({kind})", "", f"> {(bad.get('message') or '').strip()[:500]}", "",
                    "```text", *lines, "```", ""]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.stdout.write(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "tests"))
