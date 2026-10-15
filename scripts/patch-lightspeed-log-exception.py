#!/usr/bin/env python3
"""Diagnostic-only patch for debugging LCORE-4782 sanity-test 500s.

REAL EXCEPTION IDENTIFIED (2026-10-15, run 38737369532): the previous
iteration (commit c72e07e) embedded `type(exc).__name__`/`str(exc)!r` into
the response body at the confirmed swallow point, utils/agents/query.py:101
inside `map_agent_inference_error`'s `case _:` fallback. Every failing test
in that run showed:

    type=RuntimeError
    message="Attempted to exit a cancel scope that isn't the current
             tasks's current cancel scope"

This is the exact cancel-scope RuntimeError previously tracked separately
as Jira AAP-94674 (lightspeed-stack's teardown.py during agent.iter(prompt)
teardown) -- which earlier in this investigation was *thought* to be
superseded by the LCORE-4782 OGX null-delta fix (the cancel-scope noise
seen back then was attributed to a secondary sqlalchemy connection-pool
cleanup cancellation). That conclusion is now shown to be wrong, or at
least incomplete: this RuntimeError is a genuine, separate failure mode
that surfaces specifically in MCP tool-calling-heavy tests
(test_tool_filtering_lightspeed_query, test_realistic_tool_lists_trigger_filtering,
test_knowledge_search_always_included), and it is NOT a context-length
error, so `map_agent_inference_error` has nothing better to do with it
than fall through to `InternalServerErrorResponse.generic()`.

This classic anyio `RuntimeError` fires when a `CancelScope.__exit__` runs
in a different asyncio Task than the one that opened it (`self._host_task
is not current_task()`). The most common trigger is something driving an
anyio-based coroutine (e.g. an MCP client session, which uses anyio
internally for request/timeout handling) via `asyncio.wait_for`/
`asyncio.timeout`, which spawns a *new* Task to run the wrapped coroutine --
if that coroutine (or something it calls into) opened a CancelScope
expecting to control its own task lifecycle, exiting it from the wrapper's
task raises exactly this error.

We don't yet know the exact call site (lightspeed-stack's own teardown.py,
pydantic-ai's MCP toolset wrapper, or the `mcp` client SDK itself). This
patch captures the FULL traceback (via `traceback.format_exception`, using
`exc.__traceback__`, since `exc` here is the already-caught exception
instance) and embeds it in the response body -- the one diagnostic channel
proven reliable throughout this entire investigation (see the long history
in /sandbox/.claude/projects/-sandbox-ansible-chatbot-stack/memory/
project_llama_stack_0_6_upgrade.md).

See https://redhat.atlassian.net/browse/LCORE-4782 and the related
https://redhat.atlassian.net/browse/AAP-94674. Diagnostic/local-verification
only -- not meant to land upstream as-is.

UPDATE (2026-10-15): confirmed via a single-MCP-server CI experiment that
this is specifically a multi-MCP-server teardown-ordering bug, matching
https://github.com/modelcontextprotocol/python-sdk/issues/577 and
https://github.com/pydantic/pydantic-ai/issues/2818. The latter was fixed
upstream in pydantic-ai PR #4514 (merged ~April 2026), which switched
MCPServer to per-context connections so cancel scopes are always entered
and exited on the same task. check_pydantic_ai_version() below is a
build-time-only check (no source patch) to see whether the pydantic-ai/
pydantic-graph versions bundled in the lightspeed-stack:0.6.2 base image
predate that fix.
"""
import importlib.metadata
import pathlib

APP_SRC = pathlib.Path("/app-root/src")
TARGET = APP_SRC / "utils" / "agents" / "query.py"

OLD = (
    "        case RuntimeError() as runtime_exc if is_context_length_error(str(runtime_exc)):\n"
    "            return PromptTooLongResponse(model=model_id)\n"
    "        case _:\n"
    "            return InternalServerErrorResponse.generic()\n"
)
NEW = (
    "        case RuntimeError() as runtime_exc if is_context_length_error(str(runtime_exc)):\n"
    "            return PromptTooLongResponse(model=model_id)\n"
    "        case _:\n"
    "            import traceback as _lcore4782_traceback\n"
    "            _lcore4782_tb = \"\".join(\n"
    "                _lcore4782_traceback.format_exception(type(exc), exc, exc.__traceback__)\n"
    "            )\n"
    "            return InternalServerErrorResponse.query_failed(\n"
    '                f"[LCORE-4782-TRACEBACK] {_lcore4782_tb}"\n'
    "            )\n"
)


def patch_map_agent_inference_error() -> None:
    if not TARGET.exists():
        print(f"patch-lightspeed-log-exception: DIAGNOSTIC - {TARGET} does not exist")
        return
    text = TARGET.read_text()
    if "LCORE-4782-TRACEBACK" in text:
        print(f"patch-lightspeed-log-exception: {TARGET} already patched, skipping")
        return
    count = text.count(OLD)
    if count != 1:
        print(
            f"patch-lightspeed-log-exception: WARNING - expected exactly 1 occurrence of the "
            f"map_agent_inference_error fallback block in {TARGET}, found {count}; aborting"
        )
        return
    text = text.replace(OLD, NEW, 1)
    TARGET.write_text(text)
    print(f"patch-lightspeed-log-exception: embedded full traceback into map_agent_inference_error's fallback in {TARGET}")


def check_pydantic_ai_version() -> None:
    for package in ("pydantic-ai", "pydantic-graph", "anyio", "mcp"):
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            print(f"patch-lightspeed-log-exception: DIAGNOSTIC - package {package!r} not found")
            continue
        print(f"patch-lightspeed-log-exception: DIAGNOSTIC - {package}=={version}")


def main() -> int:
    patch_map_agent_inference_error()
    check_pydantic_ai_version()
    return 0  # diagnostic-only: never fail the build


if __name__ == "__main__":
    raise SystemExit(main())
