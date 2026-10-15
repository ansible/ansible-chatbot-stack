#!/usr/bin/env python3
"""Diagnostic-only patch for debugging LCORE-4782 sanity-test 500s.

ROOT CAUSE LOCATED (2026-10-15, after 8 CI cycles): the previous iteration
marked every `InternalServerErrorResponse.generic()` call site across
/app-root/src uniquely. Run 38734008481 confirmed the real one:
utils/agents/query.py:101, inside `map_agent_inference_error`:

    AgentInferenceError: TypeAlias = (
        AgentRunError | APIStatusError | APIConnectionError | RuntimeError
    )

    def map_agent_inference_error(
        exc: AgentInferenceError,
        model_id: str,
    ) -> AbstractErrorResponse:
        match exc:
            case AgentRunError() as agent_exc:
                return map_pydantic_agent_run_error(agent_exc, model_id)
            case APIStatusError() as status_exc:
                return handle_known_apistatus_errors(status_exc, model_id)
            case APIConnectionError() as connection_exc:
                return ServiceUnavailableResponse(
                    backend_name="Llama Stack", cause=str(connection_exc)
                )
            case RuntimeError() as runtime_exc if is_context_length_error(str(runtime_exc)):
                return PromptTooLongResponse(model=model_id)
            case _:
                return InternalServerErrorResponse.generic()   # <-- the real swallow point

Since AgentInferenceError only has 4 members and 3 are handled explicitly,
the ONLY way to reach `case _:` is: `exc` is a `RuntimeError` that does
NOT match `is_context_length_error(str(exc))` -- i.e. some other plain
RuntimeError is being raised during these MCP tool-calling tests, and
nothing recognizes it. This was confirmed live via the "response" field
flipping to query_failed()'s hardcoded "Error while processing query"
value and the embedded "[LCORE-4782-GENERIC path=utils/agents/query.py
line=101]" marker appearing in every failing test body.

This patch reveals the real exception's type and message by embedding
them directly in the response body (the one channel proven reliable
throughout this entire investigation -- see the long history in
/sandbox/.claude/projects/-sandbox-ansible-chatbot-stack/memory/
project_llama_stack_0_6_upgrade.md for the 7 failed side-channel attempts
that preceded this).

See https://redhat.atlassian.net/browse/LCORE-4782. Diagnostic/
local-verification only -- not meant to land upstream as-is.
"""
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
    "            return InternalServerErrorResponse.query_failed(\n"
    '                f"[LCORE-4782-REALCAUSE type={type(exc).__name__} message={str(exc)!r}] "\n'
    '                "An unexpected error occurred while processing the request."\n'
    "            )\n"
)


def patch_map_agent_inference_error() -> None:
    if not TARGET.exists():
        print(f"patch-lightspeed-log-exception: DIAGNOSTIC - {TARGET} does not exist")
        return
    text = TARGET.read_text()
    if "LCORE-4782-REALCAUSE" in text:
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
    print(f"patch-lightspeed-log-exception: embedded real exception type/message into map_agent_inference_error's fallback in {TARGET}")


def main() -> int:
    patch_map_agent_inference_error()
    return 0  # diagnostic-only: never fail the build


if __name__ == "__main__":
    raise SystemExit(main())
