"""Index a folder into a fresh catalog and print timings + a summary.

    python tools/bench_index.py LIBRARY DATA_DIR
"""

import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kurator.db import Database  # noqa: E402
from kurator.scanner import Scanner  # noqa: E402

if __name__ == "__main__":
    lib, data = sys.argv[1], sys.argv[2]
    shutil.rmtree(data, ignore_errors=True)
    db = Database(data)
    db.add_root(os.path.abspath(lib))
    s = Scanner(db)
    t = time.time()
    s.request_scan()
    while True:
        time.sleep(0.5)
        st = s.status()
        if st["phase"] == "error" or (st["phase"] == "idle" and st["finished"]):
            break
    print(f"{time.time() - t:.1f}s", st)
    c = db.cat
    print("date sources:", c.execute("SELECT date_source, COUNT(*) FROM files GROUP BY 1").fetchall())
    print("status:", c.execute("SELECT status, COUNT(*) FROM files GROUP BY 1").fetchall())
    for r in c.execute("SELECT name, error FROM files WHERE status = 2"):
        print("  error:", dict(r))
    for n in ["painting.kra", "rotated_exif6.jpg", "16bit.png", "cmyk.jpg"]:
        r = c.execute("SELECT name, width, height, date_source, datetime(date_ts,'unixepoch'), alpha "
                      "FROM files WHERE name = ?", (n,)).fetchone()
        print(" ", tuple(r) if r else (n, "missing"))
    r = c.execute("SELECT meta FROM files WHERE meta LIKE '%generation%' LIMIT 1").fetchone()
    if r:
        print("generation:", json.loads(r[0])["generation"])
    print("thumbs.db MB:", round(os.path.getsize(os.path.join(data, "thumbs.db")) / 1e6, 1))
