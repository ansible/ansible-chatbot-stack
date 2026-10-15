#!/usr/bin/env python3
"""Diagnostic-only patch for debugging LCORE-4782 sanity-test 500s.

CI run 38669905439 showed the actual failure bodies for the first time:

    {"detail":{"response":"Internal server error",
     "cause":"An unexpected error occurred while processing the request."}}

That "cause" string is the literal, hardcoded fallback message in
utils/agents/query.py's `case _:` arm of whatever function maps an
AgentFinishReason to an error response (confirmed via build-log context
dump: lines 179-182, right after handled cases for AgentFinishReason.LENGTH,
.CONTENT_FILTER and .CANCELLED). 3 of 4 sanity-test failures in that run had
this exact "cause" string, so this fallback -- not utils/query.py's
handle_known_apistatus_errors (already ruled out) -- is the real swallow
point for the remaining LCORE-4782 failures.

This patch:
  1. Dumps the full utils/agents/query.py source so the enclosing function,
     the AgentFinishReason enum, and get_agent_finish_reason()'s logic can
     be read directly instead of guessed at from log snippets.
  2. Inserts a `print(locals())` right before that fallback's return. Using
     locals() instead of a named variable means this can't NameError even
     though the match subject's variable name hasn't been confirmed yet.

See https://redhat.atlassian.net/browse/LCORE-4782. Diagnostic/
local-verification only -- not meant to land upstream as-is.
"""
import pathlib

APP_SRC = pathlib.Path("/app-root/src")
TARGET = APP_SRC / "utils" / "agents" / "query.py"

FALLBACK_OLD = (
    '            return InternalServerErrorResponse.query_failed(\n'
    '                "An unexpected error occurred while processing the request."\n'
    '            )\n'
)
FALLBACK_NEW = (
    '            print(f"LCORE-4782 diagnostic: finish-reason fallback locals={locals()!r}")\n'
    + FALLBACK_OLD
)


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


def patch_finish_reason_fallback() -> None:
    if not TARGET.exists():
        print(f"patch-lightspeed-log-exception: DIAGNOSTIC - {TARGET} does not exist")
        return
    text = TARGET.read_text()
    if "LCORE-4782 diagnostic: finish-reason fallback" in text:
        print(f"patch-lightspeed-log-exception: {TARGET} fallback logging already patched, skipping")
        return
    count = text.count(FALLBACK_OLD)
    if count != 1:
        print(
            f"patch-lightspeed-log-exception: WARNING - expected exactly 1 occurrence of the "
            f"finish-reason fallback block in {TARGET}, found {count}; skipping targeted patch"
        )
        return
    patched = text.replace(FALLBACK_OLD, FALLBACK_NEW, 1)
    TARGET.write_text(patched)
    print(f"patch-lightspeed-log-exception: inserted diagnostic print(locals()) before finish-reason fallback in {TARGET}")


def main() -> int:
    dump_file(TARGET)
    patch_finish_reason_fallback()
    return 0  # diagnostic-only: never fail the build


if __name__ == "__main__":
    raise SystemExit(main())
