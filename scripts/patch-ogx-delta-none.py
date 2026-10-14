#!/usr/bin/env python3
"""Local-verification patch for LCORE-4782.

Upstream OGX/llama-stack can raise:
    AttributeError: 'NoneType' object has no attribute 'content'
in its streaming response handling (`_process_streaming_chunks`) when a
provider sends a chat-completion chunk whose `delta` field is null (seen
with Azure OpenAI content-filter "annotation" chunks and certain terminal
chunks). See https://redhat.atlassian.net/browse/LCORE-4782.

No upstream fix exists yet (not merged in ogx-ai/ogx as of this writing).
This script was written without access to the real installed source --
it searches the venv for the file, applies the one guard pattern already
confirmed from the Jira ticket (`if chunk_choice.delta.<attr>:`), and
prints any other `chunk_choice.delta.` accesses it finds so they can be
reviewed by hand. This is meant for local reproduction/verification only,
not as a substitute for the real upstream fix.
"""
import pathlib
import re
import sys

VENV_ROOT = pathlib.Path("/app-root/.venv")
MARKER = "# LCORE-4782-patched"

SAFE_PATTERN = re.compile(r"\bif chunk_choice\.delta\.(\w+)\s*:")
ANY_DELTA_ACCESS = re.compile(r"chunk_choice\.delta\.\w+")


def guard(match: "re.Match[str]") -> str:
    attr = match.group(1)
    return f"if chunk_choice.delta is not None and chunk_choice.delta.{attr}:"


def find_target() -> pathlib.Path | None:
    candidates = sorted(VENV_ROOT.glob("**/responses/streaming.py"))
    if not candidates:
        candidates = sorted(VENV_ROOT.glob("**/streaming.py"))
    for path in candidates:
        text = path.read_text()
        if "_process_streaming_chunks" in text and "chunk_choice.delta" in text:
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

    patched, count = SAFE_PATTERN.subn(guard, text)
    if count == 0:
        print(f"patch-ogx-delta-none: no 'if chunk_choice.delta.<attr>:' pattern found in {target}", file=sys.stderr)
        return 1

    remaining = ANY_DELTA_ACCESS.findall(patched)
    if remaining:
        print(
            f"patch-ogx-delta-none: WARNING - {len(remaining)} other chunk_choice.delta.* "
            f"access(es) left unguarded in {target}: {sorted(set(remaining))}. "
            "Review manually -- the Jira ticket mentions four access points total.",
            file=sys.stderr,
        )

    target.write_text(f"{MARKER}\n{patched}")
    print(f"patch-ogx-delta-none: guarded {count} 'if chunk_choice.delta.<attr>:' occurrence(s) in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
