#!/usr/bin/env python3
"""Diagnostic-only patch for debugging LCORE-4782 sanity-test 500s.

lightspeed-stack's /v1/query error handling logs failures with
`logger.error(...)` (no exception info) instead of `logger.exception(...)`,
so the real traceback behind a generic HTTP 500
({"detail":{"response":"Internal server error", ...}}) never reaches the
logs or the response body -- confirmed in earlier debugging of this repo.
This searches the installed `lightspeed_stack` package for the file that
emits that "Internal server error" message and upgrades every
`logger.error(` call in it to `logger.exception(` so the real traceback
shows up in server logs (captured by the sanity-test pytest run) on the
next failure. Diagnostic/local-verification only -- not a real fix, and
not meant to land upstream as-is.
"""
import pathlib
import sys

VENV_ROOT = pathlib.Path("/app-root/.venv")
MARKER = "# LCORE-4782-diagnostic-patched"
NEEDLE = "Internal server error"


def find_targets() -> list[pathlib.Path]:
    candidates = sorted(VENV_ROOT.glob("**/lightspeed_stack/**/*.py"))
    return [p for p in candidates if NEEDLE in p.read_text()]


def main() -> int:
    targets = find_targets()
    if not targets:
        print(f"patch-lightspeed-log-exception: could not find any lightspeed_stack file containing {NEEDLE!r}", file=sys.stderr)
        return 1

    for target in targets:
        text = target.read_text()
        if MARKER in text:
            print(f"patch-lightspeed-log-exception: {target} already patched, skipping")
            continue

        print(f"patch-lightspeed-log-exception: DIAGNOSTIC === {target} (lines mentioning {NEEDLE!r} or logger.error) ===")
        for i, line in enumerate(text.splitlines(), start=1):
            if NEEDLE in line or "logger.error(" in line:
                print(f"{i}: {line}")
        print(f"patch-lightspeed-log-exception: DIAGNOSTIC === end {target} ===")

        count = text.count("logger.error(")
        if count == 0:
            print(f"patch-lightspeed-log-exception: no logger.error( calls in {target}, nothing to upgrade")
            continue

        patched = text.replace("logger.error(", "logger.exception(")
        patched = f"{MARKER}\n{patched}"
        target.write_text(patched)
        print(f"patch-lightspeed-log-exception: upgraded {count} logger.error( call(s) to logger.exception( in {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
