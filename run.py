#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent
    os.chdir(repo_root)

    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", str(repo_root / ".uv-cache"))

    print("[run.py] UV_LINK_MODE=copy uv sync --dev", flush=True)
    subprocess.run(
        ["uv", "sync", "--dev"],
        check=True,
        env={**env, "UV_LINK_MODE": "copy"},
    )

    print(
        "[run.py] uv run python web_console.py --host 127.0.0.1 --port 8787",
        flush=True,
    )
    return subprocess.call(
        ["uv", "run", "python", "web_console.py", "--host", "127.0.0.1", "--port", "8787"],
        env=env,
    )


if __name__ == "__main__":
    sys.exit(main())
