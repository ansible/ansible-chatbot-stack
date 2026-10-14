#!/usr/bin/env python3
"""Diagnostic-only patch for debugging LCORE-4782 sanity-test 500s.

lightspeed-stack's /v1/query error handling logs failures with
`logger.error(...)` (no exception info) instead of `logger.exception(...)`,
so the real traceback behind a generic HTTP 500
({"detail":{"response":"Internal server error", ...}}) never reaches the
logs or the response body -- confirmed in earlier debugging of this repo.

First attempt at this script looked for a file containing the literal
"Internal server error" string and failed the build when none was found
(that message is apparently assembled from a shared response model, not
inline next to the logging call) -- taking the whole CI run down with it.
This version is broader (upgrades every `logger.error(` call anywhere in
the installed `lightspeed_stack` package, not just one guessed file) and,
critically, NEVER fails the build -- this is a diagnostic aid, not a real
fix, so it must not be able to block the pipeline.

See https://redhat.atlassian.net/browse/LCORE-4782. Diagnostic/
local-verification only -- not meant to land upstream as-is.
"""
import pathlib

VENV_ROOT = pathlib.Path("/app-root/.venv")
MARKER = "# LCORE-4782-diagnostic-patched"


def main() -> int:
    candidates = sorted(VENV_ROOT.glob("**/lightspeed_stack/**/*.py"))
    print(f"patch-lightspeed-log-exception: scanned {len(candidates)} lightspeed_stack .py files under {VENV_ROOT}")

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
        print("patch-lightspeed-log-exception: WARNING - no logger.error( calls found/patched anywhere under lightspeed_stack")

    return 0  # diagnostic-only: never fail the build


if __name__ == "__main__":
    raise SystemExit(main())
