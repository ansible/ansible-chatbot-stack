#!/usr/bin/env python3
"""Diagnostic-only patch for debugging LCORE-4782 sanity-test 500s.

lightspeed-stack's /v1/query error handling logs failures with
`logger.error(...)` (no exception info) instead of `logger.exception(...)`,
so the real traceback behind a generic HTTP 500 never reaches the logs or
the response body -- confirmed in earlier debugging of this repo.

Earlier attempts assumed `lightspeed_stack` lives under
/app-root/.venv/lib/python3.12/site-packages/ like a normal pip-installed
package, or is importable as a `lightspeed_stack` package at all. A broad
scan of /app-root found the real layout: the app is plain source (not
pip-installed) directly under /app-root/src/ -- e.g.
/app-root/src/app/endpoints/query.py, /app-root/src/utils/agents/query.py,
/app-root/src/utils/query.py -- with no `lightspeed_stack` package name
anywhere in the path. This version targets /app-root/src directly. NEVER
fails the build -- this is a diagnostic aid, not a real fix.

See https://redhat.atlassian.net/browse/LCORE-4782. Diagnostic/
local-verification only -- not meant to land upstream as-is.
"""
import pathlib

APP_SRC = pathlib.Path("/app-root/src")
MARKER = "# LCORE-4782-diagnostic-patched"

# Broad logger.error->logger.exception upgrade (prior run) produced zero
# tracebacks for the 2 persistent sanity-test 500s, meaning the real swallow
# point doesn't log at all before building the generic response. Dump the
# likely culprits' full source so the real code can be read from the build
# log instead of guessed at again. utils/endpoints.py is the prime suspect:
# "endpoints" + generic-sounding name suggests shared request-handling
# helpers common to every endpoint (where a catch-all exception wrapper
# would live).
DUMP_FILES = [
    "utils/endpoints.py",
    "app/main.py",
    "app/endpoints/query.py",
    "utils/query.py",
    "utils/agents/query.py",
]


def dump_file(rel_path: str) -> None:
    path = APP_SRC / rel_path
    if not path.exists():
        print(f"patch-lightspeed-log-exception: DIAGNOSTIC - {path} does not exist")
        return
    lines = path.read_text().splitlines()
    print(f"patch-lightspeed-log-exception: DIAGNOSTIC === BEGIN {path} ({len(lines)} lines) ===")
    for i, line in enumerate(lines[:400], start=1):
        print(f"{i}: {line}")
    print(f"patch-lightspeed-log-exception: DIAGNOSTIC === END {path} ===")


def main() -> int:
    for rel_path in DUMP_FILES:
        dump_file(rel_path)

    try:
        candidates = list(APP_SRC.rglob("*.py"))
    except OSError as exc:
        print(f"patch-lightspeed-log-exception: WARNING - could not walk {APP_SRC}: {exc}")
        return 0

    print(f"patch-lightspeed-log-exception: scanned {len(candidates)} .py files under {APP_SRC}")
    if not candidates:
        print(f"patch-lightspeed-log-exception: WARNING - {APP_SRC} doesn't exist or is empty; skipping patch")
        return 0

    patched_any = False
    for target in candidates:
        try:
            text = target.read_text()
        except (OSError, UnicodeDecodeError) as exc:
            print(f"patch-lightspeed-log-exception: skipping {target} ({exc})")
            continue

        if "logger.error(" not in text:
            continue
        if MARKER in text:
            print(f"patch-lightspeed-log-exception: {target} already patched, skipping")
            continue

        count = text.count("logger.error(")
        patched = text.replace("logger.error(", "logger.exception(")
        patched = f"{MARKER}\n{patched}"
        try:
            target.write_text(patched)
        except OSError as exc:
            print(f"patch-lightspeed-log-exception: WARNING - could not write {target}: {exc}")
            continue
        patched_any = True
        print(f"patch-lightspeed-log-exception: upgraded {count} logger.error( call(s) to logger.exception( in {target}")

    if not patched_any:
        print(f"patch-lightspeed-log-exception: WARNING - scanned {APP_SRC} but found no logger.error( calls to upgrade")

    return 0  # diagnostic-only: never fail the build


if __name__ == "__main__":
    raise SystemExit(main())
