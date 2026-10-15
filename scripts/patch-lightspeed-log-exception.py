#!/usr/bin/env python3
"""Diagnostic-only patch for debugging LCORE-4782 sanity-test 500s.

BREAKTHROUGH (2026-10-15, after 7 failed CI cycles patching the wrong
function): reading the full implementation of
models/api/responses/error/internal.py (run 38730747586) showed:

    @classmethod
    def generic(cls) -> Self:
        return cls(
            response="Internal server error",
            cause="An unexpected error occurred while processing the request.",
        )

    @classmethod
    def query_failed(cls, cause: str) -> Self:
        return cls(
            response="Error while processing query",
            cause=cause,
        )

Every single observed failure body has been
`{"detail":{"response":"Internal server error","cause":"An unexpected
error occurred while processing the request."}}` -- note the "response"
field is "Internal server error", which is `generic()`'s hardcoded value,
NOT `query_failed()`'s ("Error while processing query"). We spent 5+ CI
cycles patching `get_finish_reason_error`'s `case _:` fallback in
utils/agents/query.py, which calls `query_failed(...)` -- a call site
that, if actually hit, would have produced a DIFFERENT "response" field
value we never saw even once. That whole code path was a red herring: the
`cause` text matched by coincidence (or shared wording convention) while
the `response` field -- which we never cross-checked -- did not. This
also retroactively explains why none of 5 separate diagnostics (print,
logger.error, raw stderr write, raise AssertionError, embedded response
text) ever had any effect: that line is never reached for these failures.

The real swallow point calls `InternalServerErrorResponse.generic()`
somewhere else. At least two known call sites exist (utils/query.py's
`handle_known_apistatus_errors` fallback -- previously "ruled out" via an
unreliable logger.error diagnostic we now know can't be trusted in this
codebase; and another function in utils/agents/query.py itself). Rather
than guess which one, this patch finds and marks EVERY
`return InternalServerErrorResponse.generic()` call site across the whole
/app-root/src tree, replacing each with a uniquely-identifiable
`query_failed(f"[LCORE-4782-GENERIC path=... line=...] ...")` call. Since
query_failed's "response" field differs from generic()'s, simply seeing
the "response" field flip to "Error while processing query" in the next
failing test's body will CONFIRM this diagnosis, and the embedded
path/line will show exactly which call site fired -- no more guessing.

See https://redhat.atlassian.net/browse/LCORE-4782. Diagnostic/
local-verification only -- not meant to land upstream as-is.
"""
import pathlib
import re

APP_SRC = pathlib.Path("/app-root/src")
PATTERN = re.compile(r"^(?P<indent>[ \t]*)return InternalServerErrorResponse\.generic\(\)[ \t]*$")
MARKER = "LCORE-4782-GENERIC"


def patch_generic_call_sites() -> None:
    try:
        files = sorted(APP_SRC.rglob("*.py"))
    except OSError as exc:
        print(f"patch-lightspeed-log-exception: WARNING - could not walk {APP_SRC}: {exc}")
        return

    total_patched = 0
    for path in files:
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if "InternalServerErrorResponse.generic()" not in text:
            continue
        if MARKER in text:
            print(f"patch-lightspeed-log-exception: {path} already patched, skipping")
            continue

        lines = text.splitlines(keepends=True)
        changed = False
        rel = path.relative_to(APP_SRC)
        for i, line in enumerate(lines):
            match = PATTERN.match(line.rstrip("\n"))
            if not match:
                continue
            indent = match.group("indent")
            lineno = i + 1
            new_line = (
                f'{indent}return InternalServerErrorResponse.query_failed(\n'
                f'{indent}    f"[{MARKER} path={rel} line={lineno}] "\n'
                f'{indent}    "An unexpected error occurred while processing the request."\n'
                f"{indent})\n"
            )
            lines[i] = new_line
            changed = True
            total_patched += 1
            print(f"patch-lightspeed-log-exception: marked generic() call site at {rel}:{lineno}")

        if changed:
            path.write_text("".join(lines))

    if total_patched == 0:
        print("patch-lightspeed-log-exception: WARNING - found no InternalServerErrorResponse.generic() call sites to mark")
    else:
        print(f"patch-lightspeed-log-exception: marked {total_patched} generic() call site(s) total")


def main() -> int:
    patch_generic_call_sites()
    return 0  # diagnostic-only: never fail the build


if __name__ == "__main__":
    raise SystemExit(main())
