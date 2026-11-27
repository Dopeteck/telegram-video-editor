"""Command-line entry point for manual R2 editing."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .engine import process_r2_clip


def _write_github_outputs(result: dict) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"output_key={result['output_key']}\n")
        output.write(f"manifest_key={result['manifest_key']}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="telegram-video-editor",
        description="Post-process one completed R2 clip without touching the clipper.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    process = sub.add_parser("process-r2", help="Download, transcribe, AI-plan, edit, and upload one R2 clip.")
    process.add_argument("--clip-key", required=True, help="Exact completed MP4 object key in R2.")
    process.add_argument("--job-id", required=True, help="Unique editor run identifier.")
    process.add_argument("--catalog-key", default="broll/catalog.json")
    process.add_argument("--output-prefix", default="edited")
    process.add_argument("--model", default="qwen3:4b-instruct-2507-q4_K_M")
    process.add_argument("--whisper-model", default="small")
    process.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()

    if args.command == "process-r2":
        try:
            result = process_r2_clip(
                args.clip_key, args.job_id, catalog_key=args.catalog_key,
                output_prefix=args.output_prefix, model=args.model,
                whisper_model=args.whisper_model, ollama_url=args.ollama_url,
            )
            _write_github_outputs(result)
        except Exception as exc:
            print(f"Editor failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
