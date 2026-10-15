#!/usr/bin/env python3
"""Diagnostic-only patch for debugging LCORE-4782 sanity-test 500s.

Full source of utils/agents/query.py (dumped by a prior iteration of this
script, see CI run 38672078821) shows the exact swallow point:

    class AgentFinishReason(str, Enum):
        CONTENT_FILTER = "content_filter"
        CANCELLED = "cancelled"
        SUCCESS = "stop"
        LENGTH = "length"
        ERROR = "error"

    def get_agent_finish_reason(response: ModelResponse) -> AgentFinishReason:
        raw_finish_reason = (response.provider_details or {}).get("finish_reason")
        if raw_finish_reason == "cancelled":
            return AgentFinishReason.CANCELLED
        if response.finish_reason is None:
            return AgentFinishReason.ERROR          # <-- hit for the 2 persistent 500s
        return AgentFinishReason(response.finish_reason)

    def get_finish_reason_error(finish_reason, model_id):
        match finish_reason:
            case AgentFinishReason.LENGTH: ...
            case AgentFinishReason.CONTENT_FILTER: ...
            case AgentFinishReason.CANCELLED: ...
            case _:
                return InternalServerErrorResponse.query_failed(
                    "An unexpected error occurred while processing the request."
                )                                     # <-- the literal failing body

A prior iteration tried `print(locals())` right before that fallback to see
which AgentFinishReason value falls through, but it never showed up in CI
logs even though the code path is 100% confirmed to run (the literal cause
string is unique to this one line in the whole /app-root/src tree, and it's
in every failing response body). Hypothesis at the time: bare print() is
block-buffered on a long-lived subprocess's piped stdout. Fixed by switching
to the module's own `logger.error(...)` (logging.StreamHandler flushes on
every emit) -- but run 38674481534 showed THAT didn't appear either, even
though 5 failures that run all carried the exact cause string again. Other
loggers' ERROR-level lines (e.g. `llama_stack.core.request_headers`) DID
show up fine in the same run, so the logging plumbing itself isn't broken
globally -- something specific to this module's `get_logger(__name__)`
instance (naming/handler/level config) is swallowing it silently.

Run 38676587067 showed a RAW `sys.stderr.write()` + `.flush()` (bypassing
the logging framework entirely) ALSO never appeared, despite byte-identical
failure bodies every single run. Three independent output mechanisms all
producing nothing, with an unchanged failure signature every time, is
suspicious enough to stop guessing at *why output disappears* and instead
test *whether this file is even the one executing*. entrypoint.sh runs
`$PYTHON /app-root/src/lightspeed_stack.py` directly (confirmed by reading
it locally in this repo), which should put /app-root/src at the front of
sys.path and make our edits authoritative -- but we've never actually
proven that assumption.

So: replace the quiet `return AgentFinishReason.ERROR` with a loud,
distinctive `raise AssertionError(...)` instead. This is a definitive
test, not another logging attempt:
  - If this file IS what's running, the failure signature MUST change --
    an AssertionError isn't caught by app/endpoints/query.py's 3 specific
    except clauses (RuntimeError, APIConnectionError, LLSApiStatusError/
    OpenAIAPIStatusError), so it will propagate to the global ASGI
    exception middleware in app/main.py, which does
    `logger.exception("Uncaught exception in endpoint: %s", exc)` --
    a message that has NEVER appeared in any run so far. Seeing it for
    the first time, or seeing a differently-shaped 500 body, proves the
    patch is live and the exception message will hand us
    raw_finish_reason/provider_details directly.
  - If the response body stays byte-identical to every prior run despite
    this change, that's proof this patched file is NOT what the running
    container executes -- the real swallow point is coming from somewhere
    else entirely (a different installed copy of this module, or a
    completely different code path that happens to produce the same
    wording), and the next step is to find where the app's lightspeed-stack
    package is actually imported from at runtime, not to keep patching
    /app-root/src blind.

Suspected cause worth cross-checking once real data is in hand: the
LCORE-4782 OGX fix itself (scripts/patch-ogx-delta-none.py) inserts
`if chunk_choice.delta is None: continue` immediately after
`for chunk_choice in chunk.choices:` in OGX's streaming.py. But
`chunk_choice.finish_reason` is read later in that SAME loop body
(streaming.py:995-996, after the delta-dependent code) -- so for any
provider chunk where `delta` is null on the SAME chunk_choice that also
carries the real finish_reason (e.g. Azure's terminal/content-filter
chunk shape), our `continue` now skips capturing it too. That might be
a self-inflicted regression, not a new/different OGX bug. Don't patch
streaming.py again until this diagnostic confirms raw_finish_reason /
provider_details are actually empty for the failing case -- confirm
before fixing.

Run 38678790918: the raise WAS applied cleanly (build log confirmed it),
but neither "LCORE-4782-MARKER" nor app/main.py's "Uncaught exception in
endpoint" appeared anywhere, and the failure body was STILL byte-identical
to every prior run. This is airtight: /app-root/src/utils/agents/query.py
is NOT the code executing at runtime -- patching it further is pointless
until we find where the real, live copy of this module actually is.

This iteration adds a build-time import-resolution check, run with the
exact same interpreter/venv the container will use at runtime (`uv run
python`). It mimics what `entrypoint.sh` does when it runs
`$PYTHON /app-root/src/lightspeed_stack.py` directly (which makes Python
put that script's own directory at sys.path[0]) by inserting
"/app-root/src" at the front of sys.path, then importing
`utils.agents.query` and printing its resolved `__file__` plus whether our
MARKER text is present in the source Python actually loaded. If this
resolves to a different path than our patched file (or the loaded source
lacks our edits even though it resolves to the same path -- e.g. a stale
compiled artifact), that tells us definitively where to look next instead
of guessing further.

See https://redhat.atlassian.net/browse/LCORE-4782. Diagnostic/
local-verification only -- not meant to land upstream as-is.
"""
import importlib
import pathlib
import sys
import traceback

APP_SRC = pathlib.Path("/app-root/src")
TARGET = APP_SRC / "utils" / "agents" / "query.py"

OLD = (
    "    if response.finish_reason is None:\n"
    "        return AgentFinishReason.ERROR\n"
)
NEW = (
    "    if response.finish_reason is None:\n"
    '        raise AssertionError(\n'
    '            "LCORE-4782-MARKER: finish_reason is None - '
    'raw_finish_reason=%r provider_details=%r"\n'
    "            % (raw_finish_reason, response.provider_details)\n"
    "        )\n"
)


def patch_finish_reason_none_branch() -> None:
    if not TARGET.exists():
        print(f"patch-lightspeed-log-exception: DIAGNOSTIC - {TARGET} does not exist")
        return
    text = TARGET.read_text()
    if "LCORE-4782 diagnostic: finish_reason is None" in text:
        print(f"patch-lightspeed-log-exception: {TARGET} already patched, skipping")
        return
    count = text.count(OLD)
    if count != 1:
        print(
            f"patch-lightspeed-log-exception: WARNING - expected exactly 1 occurrence of the "
            f"'finish_reason is None' branch in {TARGET}, found {count}; skipping targeted patch"
        )
        return
    patched = text.replace(OLD, NEW, 1)
    TARGET.write_text(patched)
    print(f"patch-lightspeed-log-exception: inserted raise AssertionError('LCORE-4782-MARKER...') in the finish_reason-is-None branch of {TARGET}")


def check_import_resolution() -> None:
    """Prove (or disprove) that /app-root/src is what Python actually
    imports from at runtime, using the exact interpreter/venv that will
    run the app, mimicking entrypoint.sh's own sys.path[0] setup."""
    src_str = str(APP_SRC)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)
    try:
        module = importlib.import_module("utils.agents.query")
    except Exception:
        print("patch-lightspeed-log-exception: DIAGNOSTIC - import utils.agents.query FAILED:")
        traceback.print_exc()
        return

    resolved = getattr(module, "__file__", None)
    print(f"patch-lightspeed-log-exception: DIAGNOSTIC - utils.agents.query resolves to {resolved!r}")
    print(f"patch-lightspeed-log-exception: DIAGNOSTIC - expected target was {TARGET}")
    print(f"patch-lightspeed-log-exception: DIAGNOSTIC - resolved path matches expected target: {resolved == str(TARGET)}")

    if resolved:
        try:
            resolved_text = pathlib.Path(resolved).read_text()
        except OSError as exc:
            print(f"patch-lightspeed-log-exception: DIAGNOSTIC - could not read resolved file: {exc}")
        else:
            print(
                "patch-lightspeed-log-exception: DIAGNOSTIC - resolved file contains LCORE-4782-MARKER: "
                f"{'LCORE-4782-MARKER' in resolved_text}"
            )

    print(f"patch-lightspeed-log-exception: DIAGNOSTIC - full sys.path: {sys.path}")


def main() -> int:
    patch_finish_reason_none_branch()
    check_import_resolution()
    return 0  # diagnostic-only: never fail the build


if __name__ == "__main__":
    raise SystemExit(main())
