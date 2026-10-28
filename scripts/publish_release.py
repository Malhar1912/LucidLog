#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env=env)


def build_and_validate() -> None:
    run([sys.executable, "-m", "build"])
    run([sys.executable, "-m", "twine", "check", "dist/*"])


def publish(repository: str) -> None:
    run([sys.executable, "-m", "twine", "upload", "dist/*", "--repository", repository])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and optionally publish the LucidLog package.")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip the local build and validation step and publish the existing dist files.",
    )
    parser.add_argument(
        "--repository",
        choices=["pypi", "testpypi"],
        default="pypi",
        help="Target repository for uploading the package.",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Build and validate only; do not upload to the package index.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("[1/2] Building and validating package artifacts...")
    if not args.skip_build:
        build_and_validate()
    else:
        print("Skipping build step; using existing dist artifacts.")

    if args.no_upload:
        print("[2/2] Upload skipped by request.")
        return

    print(f"[2/2] Uploading to {args.repository}...")
    publish(args.repository)
    print("Release published successfully.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}.", file=sys.stderr)
        sys.exit(exc.returncode)
