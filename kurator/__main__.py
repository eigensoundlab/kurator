"""Command line entry point:  python -m kurator [--library PATH ...]"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import time
import webbrowser


def default_data_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "Kurator")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Kurator")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "kurator")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kurator", description="Fast local asset manager.")
    ap.add_argument("--library", "-l", action="append", default=[], metavar="FOLDER",
                    help="add a library folder (can be repeated; remembered between runs)")
    ap.add_argument("--data-dir", default=default_data_dir(),
                    help="where the catalog and thumbnail cache live (default: %(default)s)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--workers", type=int, default=None, help="indexing processes (default: CPU cores - 1)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap.add_argument("--no-scan", action="store_true", help="don't rescan library folders on start")
    ap.add_argument("--no-watch", action="store_true", help="don't watch folders for changes (use Rescan instead)")
    ap.add_argument("--no-ai", action="store_true", help="switch off the AI features")
    args = ap.parse_args(argv)

    import uvicorn

    from .db import Database
    from .scanner import Scanner
    from .server import create_app
    from .watcher import Watcher

    db = Database(os.path.abspath(args.data_dir))
    db.cleanup()
    for lib in args.library:
        p = os.path.normpath(os.path.abspath(os.path.expanduser(lib)))
        if not os.path.isdir(p):
            ap.error(f"not a folder: {p}")
        if not db.root_for(p, "library"):
            db.add_root(p, "library")

    scanner = Scanner(db, workers=args.workers)
    watcher = None if args.no_watch else Watcher(db, scanner)
    ai = None
    if not args.no_ai:
        from .ai import AIService
        ai = AIService(db)  # models load only when an AI feature is used
    app = create_app(db, scanner, args.port, watcher, ai)
    if watcher:
        watcher.start()
    if not args.no_scan and db.roots():
        scanner.request_scan()

    url = f"http://127.0.0.1:{args.port}/"
    print(f"Kurator running at {url}  (data: {db.data_dir})  Ctrl+C to quit")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.0), webbrowser.open(url)), daemon=True).start()
    # Loopback only - the API can read your library, it must never be reachable from the network.
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    async def serve():
        loop = asyncio.get_running_loop()
        default = loop.get_exception_handler()

        def quiet(loop, context):
            # The grid cancels image downloads on purpose while scrolling. On Windows the
            # Proactor event loop then logs a harmless ConnectionResetError (WinError 10054).
            if isinstance(context.get("exception"), (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
                return
            if default:
                default(loop, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(quiet)
        await server.serve()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
