#!/usr/bin/env python3
"""Scrollback: a personal feed of historical events.

Run:  python3 scrollback.py            (then open http://localhost:8765)
      python3 scrollback.py --open     (also opens your browser)
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser

if sys.version_info < (3, 10):
    sys.exit("Scrollback needs Python 3.10 or newer.")

from scrollback_core import __version__, config, db  # noqa: E402
from scrollback_core.server import App, serve  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrollback: a personal feed of historical events")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="keep this on localhost unless you know why")
    ap.add_argument("--open", action="store_true", help="open the app in your browser")
    ap.add_argument("--no-crawl", action="store_true", help="do not fetch from Wikipedia")
    ap.add_argument("--fetch-day", metavar="MM-DD", help="fetch one day from Wikipedia and exit")
    ap.add_argument("--download-voice", metavar="VOICE", help="download a Piper voice, such as en_GB-cori-medium")
    ap.add_argument("--tts-test", action="store_true", help="say a test sentence with Piper and exit")
    ap.add_argument("--build-demo", metavar="DIR", help="build the browser demo site into DIR and exit")
    ap.add_argument("--demo-url", metavar="URL", help="public address of the demo, for link previews")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.build_demo:
        from scrollback_core import demo_build
        count = demo_build.build(args.build_demo, url=args.demo_url or demo_build.DEMO_URL)
        print(f"Built the demo with {count} events in {args.build_demo}")
        return

    db.init()

    if args.fetch_day:
        from scrollback_core.wiki import ingest_day
        month, day = (int(x) for x in args.fetch_day.split("-"))
        added = ingest_day(month, day)
        print(f"Added {added} new posts for {month:02d}-{day:02d}.")
        return

    if args.download_voice:
        from scrollback_core import tts

        def show(done: int, total: int) -> None:
            if total:
                print(f"\r  {done / 1e6:5.1f} of {total / 1e6:.1f} MB", end="", flush=True)
        print(f"Downloading {args.download_voice} from Hugging Face")
        path = tts.download_voice(args.download_voice, progress=show)
        if not db.get_setting("tts_voice"):
            db.set_setting("tts_voice", args.download_voice)
        print(f"\nSaved {path}")
        return

    if args.tts_test:
        from scrollback_core import tts
        engine = tts.Engine()
        status = engine.status()
        if not status["available"]:
            sys.exit(f"Text to speech is not ready: {status['reason']}")
        print(f"Using {status['voice']} with {status['python']}")
        path = engine.synthesize("Scrollback can read history aloud. The fourteenth of October, ten sixty-six.")
        engine.shutdown()
        print(f"Wrote {path}")
        return

    app = App(crawl=not args.no_crawl)
    app.start_workers()
    httpd = serve(args.host, args.port, app)
    url = f"http://localhost:{args.port}/"
    print(f"Scrollback {__version__} running at {url}")
    print(f"Data: {config.db_path()}")
    print("Press Ctrl+C to stop.")
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
        app.tts.shutdown()


if __name__ == "__main__":
    main()
