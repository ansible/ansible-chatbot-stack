#!/usr/bin/env python3
"""Diagnostic-only patch for debugging LCORE-4782 sanity-test 500s.

Full source of utils/agents/query.py shows the exact swallow point:

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
            return AgentFinishReason.ERROR          # <-- hit for the persistent 500s
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

Four separate attempts to get a side-channel diagnostic out of this branch
all failed to surface anything, despite the exact literal cause string
proving the branch fires every single run:
  1. print(locals()) -- never appeared (suspected stdout buffering).
  2. logger.error(...) -- never appeared, even though OTHER loggers'
     ERROR-level lines (llama_stack.core.request_headers) showed up fine
     in the same run.
  3. raw sys.stderr.write() + .flush() -- bypasses the logging framework
     entirely, still never appeared.
  4. raise AssertionError("LCORE-4782-MARKER...") instead of returning --
     a structurally different signal that can't be silently swallowed by
     a broken stdout/logger config. Still never appeared, and the failure
     body stayed byte-identical to every prior run (run 38678790918).

A build-time import-resolution check (run 38723780554, via `uv run
python`, the exact interpreter/venv the container uses) then PROVED the
file is correctly resolved and contains our edits: "utils.agents.query
resolves to '/app-root/src/utils/agents/query.py'", "matches expected
target: True", "contains LCORE-4782-MARKER: True". So the file is live.
Something between that raise and the final HTTP response must be
catching it (and anything else) and reconstructing this exact same
generic message -- which also means none of the four output mechanisms
above had a chance to matter once that catch-and-reconstruct happens,
except they should still have produced output from the WRITE statement
itself, synchronously, before any later code could intervene. That they
didn't is still unexplained, but chasing a fifth stdout/logger mechanism
isn't a good use of another ~20min CI cycle.

New approach: stop trying to get data out via a side channel (stdout,
stderr, logger) entirely. Smuggle the diagnostic directly into the HTTP
response body instead, via a module-level dict that get_agent_finish_reason
writes into and get_finish_reason_error's case _: reads back when building
its message. The response body / pytest assertion text is the one channel
that has reliably surfaced in 100% of runs so far (that's literally how
we've been reading results this whole investigation) -- no separate
stdout/stderr capture, buffering, or logger config can interfere with it.

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

See https://redhat.atlassian.net/browse/LCORE-4782. Diagnostic/
local-verification only -- not meant to land upstream as-is.
"""
import pathlib

APP_SRC = pathlib.Path("/app-root/src")
TARGET = APP_SRC / "utils" / "agents" / "query.py"

LOGGER_LINE = "logger = get_logger(__name__)\n"
LOGGER_LINE_PATCHED = LOGGER_LINE + "_LCORE4782_DEBUG: dict = {}\n"

FINISH_REASON_NONE_OLD = (
    "    if response.finish_reason is None:\n"
    "        return AgentFinishReason.ERROR\n"
)
FINISH_REASON_NONE_NEW = (
    "    if response.finish_reason is None:\n"
    '        _LCORE4782_DEBUG["raw_finish_reason"] = raw_finish_reason\n'
    '        _LCORE4782_DEBUG["provider_details"] = response.provider_details\n'
    "        return AgentFinishReason.ERROR\n"
)

FALLBACK_OLD = (
    "        case _:\n"
    "            return InternalServerErrorResponse.query_failed(\n"
    '                "An unexpected error occurred while processing the request."\n'
    "            )\n"
)
FALLBACK_NEW = (
    "        case _:\n"
    "            return InternalServerErrorResponse.query_failed(\n"
    '                "An unexpected error occurred while processing the request. "\n'
    '                f"[LCORE-4782 finish_reason={finish_reason!r} debug={_LCORE4782_DEBUG!r}]"\n'
    "            )\n"
)


def patch_finish_reason_diagnostics() -> None:
    if not TARGET.exists():
        print(f"patch-lightspeed-log-exception: DIAGNOSTIC - {TARGET} does not exist")
        return
    text = TARGET.read_text()
    if "_LCORE4782_DEBUG" in text:
        print(f"patch-lightspeed-log-exception: {TARGET} already patched, skipping")
        return

    if LOGGER_LINE not in text:
        print(f"patch-lightspeed-log-exception: WARNING - could not find logger line anchor in {TARGET}; aborting")
        return
    if text.count(LOGGER_LINE) != 1:
        print(f"patch-lightspeed-log-exception: WARNING - logger line anchor not unique in {TARGET}; aborting")
        return
    text = text.replace(LOGGER_LINE, LOGGER_LINE_PATCHED, 1)

    if text.count(FINISH_REASON_NONE_OLD) != 1:
        print(
            f"patch-lightspeed-log-exception: WARNING - expected exactly 1 occurrence of the "
            f"finish_reason-is-None branch in {TARGET}, found {text.count(FINISH_REASON_NONE_OLD)}; aborting"
        )
        return
    text = text.replace(FINISH_REASON_NONE_OLD, FINISH_REASON_NONE_NEW, 1)

    if text.count(FALLBACK_OLD) != 1:
        print(
            f"patch-lightspeed-log-exception: WARNING - expected exactly 1 occurrence of the "
            f"generic fallback block in {TARGET}, found {text.count(FALLBACK_OLD)}; aborting"
        )
        return
    text = text.replace(FALLBACK_OLD, FALLBACK_NEW, 1)

    TARGET.write_text(text)
    print(f"patch-lightspeed-log-exception: embedded finish_reason diagnostic info into the response body fallback in {TARGET}")


def main() -> int:
    patch_finish_reason_diagnostics()
    return 0  # diagnostic-only: never fail the build


if __name__ == "__main__":
    raise SystemExit(main())
