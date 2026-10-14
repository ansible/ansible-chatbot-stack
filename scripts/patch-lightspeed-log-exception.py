#!/usr/bin/env python3
"""Diagnostic-only patch for debugging LCORE-4782 sanity-test 500s.

lightspeed-stack's /v1/query error handling logs failures with
`logger.error(...)` (no exception info) instead of `logger.exception(...)`,
so the real traceback behind a generic HTTP 500 never reaches the logs or
the response body -- confirmed in earlier debugging of this repo.

Earlier attempts assumed `lightspeed_stack` lives under
/app-root/.venv/lib/python3.12/site-packages/ like a normal pip-installed
package. A broad scan found ZERO files there, so the app must be laid out
differently in this base image (likely copied in as source rather than
pip-installed, or under a different venv/site-packages path). This version
searches all of /app-root first to find where `lightspeed_stack` (or
`query.py` / an `endpoints` dir, per prior debugging) actually lives, prints
what it finds, and only then attempts the logger.error -> logger.exception
upgrade on whatever .py files it locates. NEVER fails the build -- this is
a diagnostic aid, not a real fix.

See https://redhat.atlassian.net/browse/LCORE-4782. Diagnostic/
local-verification only -- not meant to land upstream as-is.
"""
import pathlib

APP_ROOT = pathlib.Path("/app-root")
MARKER = "# LCORE-4782-diagnostic-patched"


def main() -> int:
    try:
        all_py_files = list(APP_ROOT.rglob("*.py"))
    except OSError as exc:
        print(f"patch-lightspeed-log-exception: WARNING - could not walk {APP_ROOT}: {exc}")
        return 0

    print(f"patch-lightspeed-log-exception: scanned {len(all_py_files)} total .py files under {APP_ROOT}")

    lightspeed_dirs = sorted({p.parent for p in all_py_files if "lightspeed_stack" in p.parts})
    print(f"patch-lightspeed-log-exception: DIAGNOSTIC - directories on a 'lightspeed_stack' path: {[str(d) for d in lightspeed_dirs[:40]]}")

    query_files = sorted(p for p in all_py_files if p.name == "query.py")
    print(f"patch-lightspeed-log-exception: DIAGNOSTIC - files named query.py: {[str(p) for p in query_files]}")

    endpoints_files = sorted(p for p in all_py_files if "endpoints" in p.parts)
    print(f"patch-lightspeed-log-exception: DIAGNOSTIC - files under an 'endpoints' dir: {[str(p) for p in endpoints_files[:40]]}")

    unexpected_error_files = []
    for p in all_py_files:
        try:
            if "unexpected error" in p.read_text().lower():
                unexpected_error_files.append(p)
        except (OSError, UnicodeDecodeError):
            continue
        if len(unexpected_error_files) >= 20:
            break
    print(f"patch-lightspeed-log-exception: DIAGNOSTIC - files mentioning 'unexpected error' (case-insensitive, first 20): {[str(p) for p in unexpected_error_files]}")

    candidates = [p for p in all_py_files if "lightspeed_stack" in p.parts]
    if not candidates:
        print("patch-lightspeed-log-exception: WARNING - no .py files on a lightspeed_stack path found anywhere under /app-root; skipping patch")
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
        print("patch-lightspeed-log-exception: WARNING - found lightspeed_stack .py files but no logger.error( calls to upgrade")

    return 0  # diagnostic-only: never fail the build


if __name__ == "__main__":
    raise SystemExit(main())
