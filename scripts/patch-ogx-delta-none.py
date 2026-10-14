#!/usr/bin/env python3
"""Local-verification patch for LCORE-4782.

Upstream OGX/llama-stack raises:
    AttributeError: 'NoneType' object has no attribute 'content'
in `_process_streaming_chunks` (streaming.py) when a provider sends a
chat-completion chunk whose `delta` field is null (seen with Azure OpenAI
content-filter "annotation" chunks and certain terminal chunks). The
function's `for chunk_choice in chunk.choices:` loop never checks whether
`chunk_choice.delta` itself is None before touching `.content`, `.reasoning`,
`.refusal`, and `.tool_calls` on it -- several of those accesses (e.g. the
unconditional `chat_response_content.append(chunk_choice.delta.content or "")`)
run every iteration regardless of any earlier `if chunk_choice.delta.X:`
check, so guarding individual fields one at a time isn't sufficient.

See https://redhat.atlassian.net/browse/LCORE-4782. No upstream fix merged
yet. This mirrors the fix pydantic-ai applied to the same chunk shape on its
own client-side code (`if choice.delta is None: continue`), applied here to
OGX's server-side loop instead. For local reproduction/verification only.
"""
import pathlib
import re
import sys

VENV_ROOT = pathlib.Path("/app-root/.venv")
MARKER = "# LCORE-4782-patched"
LOOP_LINE = "for chunk_choice in chunk.choices:"


def find_target() -> pathlib.Path | None:
    candidates = sorted(VENV_ROOT.glob("**/responses/streaming.py"))
    if not candidates:
        candidates = sorted(VENV_ROOT.glob("**/streaming.py"))
    for path in candidates:
        text = path.read_text()
        if "_process_streaming_chunks" in text and LOOP_LINE in text:
            return path
    return None


def main() -> int:
    target = find_target()
    if target is None:
        print(f"patch-ogx-delta-none: could not locate OGX streaming.py under {VENV_ROOT}", file=sys.stderr)
        return 1

    text = target.read_text()
    if MARKER in text:
        print(f"patch-ogx-delta-none: {target} already patched, skipping")
        return 0

    pattern = re.compile(r"^([ \t]*)" + re.escape(LOOP_LINE) + r"\s*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        print(
            f"patch-ogx-delta-none: expected exactly one '{LOOP_LINE}' in {target}, found {len(matches)}",
            file=sys.stderr,
        )
        return 1

    match = matches[0]
    indent = match.group(1)
    guard = f"{indent}    if chunk_choice.delta is None:\n{indent}        continue\n"
    insert_at = match.end() + 1  # past the newline ending the for-loop line
    patched = text[:insert_at] + guard + text[insert_at:]
    patched = f"{MARKER}\n{patched}"

    target.write_text(patched)
    print(f"patch-ogx-delta-none: inserted 'chunk_choice.delta is None: continue' guard in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
