#!/usr/bin/env python3
"""Diagnostic-only patch for debugging LCORE-4782 sanity-test 500s.

Root-caused the swallow point: app/endpoints/query.py catches
(LLSApiStatusError, OpenAIAPIStatusError) and hands them to
handle_known_apistatus_errors() in utils/query.py, which is:

    error_message = getattr(error, "message", str(error))
    if is_context_length_error(error_message):
        return PromptTooLongResponse(model=model_id)
    if error.status_code == 429:
        return QuotaExceededResponse.model(model_id)
    return InternalServerErrorResponse.generic()

Any API status error that isn't a context-length error or a 429 falls
through to a generic response with ZERO logging -- by design, not a bug.
That's why broadly upgrading logger.error->logger.exception elsewhere
(still done below, harmless) found nothing: there was never a logging
call on this fallback path to upgrade. This inserts a new logger.error(
call right before the generic-fallback return, capturing the real
status_code/message/body/repr of the discarded error, to find out what's
actually causing test_tool_filtering_lightspeed_query[openai] and
test_knowledge_search_always_included[azure] to 500 on every CI run.

See https://redhat.atlassian.net/browse/LCORE-4782. Diagnostic/
local-verification only -- not meant to land upstream as-is.
"""
import pathlib

APP_SRC = pathlib.Path("/app-root/src")
MARKER = "# LCORE-4782-diagnostic-patched"

FALLBACK_FILE = APP_SRC / "utils" / "query.py"
FALLBACK_LINE = "    return InternalServerErrorResponse.generic()"
FALLBACK_LOG = (
    '    logger.error("LCORE-4782 diagnostic: generic-fallback status_code=%s '
    'message=%s body=%r repr=%r", getattr(error, "status_code", None), '
    'error_message, getattr(error, "body", None), error)\n'
)


def patch_fallback_logging() -> None:
    if not FALLBACK_FILE.exists():
        print(f"patch-lightspeed-log-exception: DIAGNOSTIC - {FALLBACK_FILE} does not exist")
        return
    text = FALLBACK_FILE.read_text()
    if "LCORE-4782 diagnostic: generic-fallback" in text:
        print(f"patch-lightspeed-log-exception: {FALLBACK_FILE} fallback logging already patched, skipping")
        return
    count = text.count(FALLBACK_LINE)
    if count != 1:
        print(
            f"patch-lightspeed-log-exception: WARNING - expected exactly 1 occurrence of "
            f"{FALLBACK_LINE!r} in {FALLBACK_FILE}, found {count}; skipping targeted patch"
        )
        return
    patched = text.replace(FALLBACK_LINE, FALLBACK_LOG + FALLBACK_LINE, 1)
    FALLBACK_FILE.write_text(patched)
    print(f"patch-lightspeed-log-exception: inserted diagnostic logger.error( before generic fallback in {FALLBACK_FILE}")


def main() -> int:
    patch_fallback_logging()

    try:
        candidates = list(APP_SRC.rglob("*.py"))
    except OSError as exc:
        print(f"patch-lightspeed-log-exception: WARNING - could not walk {APP_SRC}: {exc}")
        return 0

    print(f"patch-lightspeed-log-exception: scanned {len(candidates)} .py files under {APP_SRC}")

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
        print(f"patch-lightspeed-log-exception: WARNING - scanned {APP_SRC} but found no logger.error( calls to upgrade")

    return 0  # diagnostic-only: never fail the build


if __name__ == "__main__":
    raise SystemExit(main())
