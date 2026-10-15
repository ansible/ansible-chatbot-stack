#!/usr/bin/env python3
"""Diagnostic-only patch for debugging LCORE-4782 sanity-test 500s.

Run 38726461678 broke the prior theory entirely: we embedded a debug
suffix directly into the STRING LITERAL passed to
`InternalServerErrorResponse.query_failed(...)` in the `case _:` fallback
of `get_finish_reason_error` (utils/agents/query.py). The build log
confirmed the text replacement applied cleanly. Yet the failing tests'
response bodies were STILL the exact old, un-suffixed
"An unexpected error occurred while processing the request." -- no
`[LCORE-4782 finish_reason=... debug=...]` suffix anywhere.

This is a much bigger clue than the previous 4 output-channel failures:
embedding text into a string literal that's passed straight into the
final response payload bypasses stdout, stderr, logging, and buffering
entirely. If that text still doesn't appear, the simplest explanation is
that `InternalServerErrorResponse.query_failed(cause)` does NOT actually
use its `cause` argument -- i.e. it's a bug in query_failed() itself,
always returning the same hardcoded message regardless of input. We have
only ever seen this classmethod's SIGNATURE (`def query_failed(cls, cause:
str) -> Self:`) from an earlier partial dump, never its body.

This iteration is pure read-only diagnosis (no functional patch) -- dump
the full content of:
  - models/api/responses/error/internal.py (InternalServerErrorResponse,
    including query_failed() and generic()'s actual bodies)
  - models/api/responses/error/bases.py (AbstractErrorResponse, in case
    there's a validator/default overriding fields at construction time)
  - models/api/responses/error/__init__.py (how these get re-exported)
to see exactly what happens to the `cause` argument end to end.

See https://redhat.atlassian.net/browse/LCORE-4782. Diagnostic/
local-verification only -- not meant to land upstream as-is.
"""
import pathlib

APP_SRC = pathlib.Path("/app-root/src")
ERROR_DIR = APP_SRC / "models" / "api" / "responses" / "error"


def dump_file(path: pathlib.Path, cap: int = 1000) -> None:
    if not path.exists():
        print(f"patch-lightspeed-log-exception: DIAGNOSTIC - {path} does not exist")
        return
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"patch-lightspeed-log-exception: DIAGNOSTIC - could not read {path}: {exc}")
        return
    print(f"patch-lightspeed-log-exception: DIAGNOSTIC === BEGIN {path} ({len(lines)} lines) ===")
    for i, line in enumerate(lines[:cap]):
        print(f"{i + 1}: {line}")
    if len(lines) > cap:
        print(f"patch-lightspeed-log-exception: DIAGNOSTIC - ... truncated, {len(lines) - cap} more lines")
    print(f"patch-lightspeed-log-exception: DIAGNOSTIC === END {path} ===")


def main() -> int:
    dump_file(ERROR_DIR / "internal.py")
    dump_file(ERROR_DIR / "bases.py")
    dump_file(ERROR_DIR / "__init__.py")
    return 0  # diagnostic-only: never fail the build


if __name__ == "__main__":
    raise SystemExit(main())
