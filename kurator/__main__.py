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
    ver = db.get_setting("previews.audio", 0)
    if ver < 3:   # once: orange waveforms for long sounds (0.9); loudness, tempo and key (0.11)
        db.remake_previews("kind = 'audio'")
        db.set_setting("previews.audio", 3)
    if db.get_setting("previews.models", 0) < 2:   # once (0.12): textured previews, texture lists for the filter
        db.remake_previews("kind = 'model' AND json_extract(meta, '$.mesh') = 1")
        db.set_setting("previews.models", 2)
    os.environ["KURATOR_SVG_ALL"] = "1" if db.get_setting("svg_all", False) else "0"
    from . import __version__
    if db.get_setting("errors.retried", "") != __version__:   # a new version may read files the old one couldn't
        with db.write_lock:
            db.cat.execute("UPDATE files SET status = 0 WHERE status = 2")
            db.cat.commit()
        db.set_setting("errors.retried", __version__)
    from .handlers.video import tool
    if tool("ffmpeg") and tool("ffprobe"):   # ffmpeg installed since videos were indexed: real previews now
        n = db.remake_previews("kind = 'video' AND (json_extract(meta, '$.placeholder') IS NOT NULL "
                               "OR json_extract(meta, '$.preview') = 'windows')")
        if n:
            print(f"[Kurator] ffmpeg found: making previews for {n} videos")
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

    # fill in picture fingerprints and colours for catalogs from older versions, once things are calm
    later = threading.Timer(20, app.state.backfill.start)
    later.daemon = True
    later.start()
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Kurator running at {url}  (data: {db.data_dir})  Ctrl+C to quit")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.0), webbrowser.open(url)), daemon=True).start()
    # Loopback only - the API can read your library, it must never be reachable from the network.
    # timeout_graceful_shutdown: don't wait for the browser's open connections when quitting
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False,
                            timeout_graceful_shutdown=2)
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
    finally:
        _quit(scanner, watcher, ai)


def _quit(scanner, watcher, ai) -> None:
    """Stop background work quickly, then exit without waiting for queued work.

    Everything Kurator writes is committed as it goes (SQLite, WAL), so nothing is lost by not
    finishing a scan: unfinished files are simply processed on the next start.
    """
    print("[Kurator] Stopping ...", flush=True)
    done = threading.Event()

    from . import rclone

    def stop_all():
        for fn in (lambda: scanner.stop(timeout=3),
                   lambda: watcher and watcher.stop(),
                   lambda: ai and ai.cancel(),
                   lambda: rclone._shared and rclone._shared.stop()):
            try:
                fn()
            except Exception:
                pass
        done.set()

    threading.Thread(target=stop_all, daemon=True).start()
    done.wait(6)
    print("[Kurator] Stopped.", flush=True)
    # os._exit: skip interpreter shutdown, which would otherwise wait for every queued thumbnail
    # or scan job in the thread pools (minutes on a big library).
    os._exit(0)


if __name__ == "__main__":
    main()
