from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


PROMPT_TEMPLATE = """You are solving one SWE-Bench Pro instance.

Repository: {repo}
Language: {repo_language}
Instance ID: {instance_id}

Problem statement:
{problem_statement}

Requirements:
{requirements}

Interface:
{interface}

Fail-to-pass tests:
{fail_to_pass}

Pass-to-pass tests:
{pass_to_pass}

Return only a valid unified git diff patch.
Do not include explanations.
Do not include markdown unless the entire answer is a single ```diff fenced block.
If you cannot produce a patch, return an empty response.
"""


def extract_patch(text: str) -> str:
    fence_match = re.search(r"```diff\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip() + "\n"
    diff_index = text.find("diff --git ")
    if diff_index >= 0:
        return text[diff_index:].strip() + "\n"
    return ""


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def call_model(base_url: str, api_key: str, model: str, prompt: str, timeout: int) -> tuple[str, dict]:
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        url=f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8"))
    latency_sec = time.time() - started
    return raw["choices"][0]["message"]["content"], {
        "usage": raw.get("usage", {}),
        "latency_sec": latency_sec,
    }


def call_model_stream(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: int,
    instance_id: str,
) -> tuple[str, dict]:
    payload = {
        "model": model,
        "temperature": 0,
        "stream": True,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        url=f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    started = time.time()
    first_chunk_at = None
    chunk_count = 0
    content_parts: list[str] = []
    last_progress_chars = 0

    with urllib.request.urlopen(request, timeout=timeout) as response:
        while True:
            raw_line = response.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue

            data = line[5:].strip()
            if data == "[DONE]":
                break

            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue

            choice = (payload.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            text = delta.get("content") or ""
            if text:
                chunk_count += 1
                if first_chunk_at is None:
                    first_chunk_at = time.time()
                    log(
                        f"{instance_id}: first stream chunk after "
                        f"{first_chunk_at - started:.2f}s"
                    )
                content_parts.append(text)
                total_chars = sum(len(part) for part in content_parts)
                if total_chars - last_progress_chars >= 800:
                    last_progress_chars = total_chars
                    log(
                        f"{instance_id}: streaming progress "
                        f"chars={total_chars} chunks={chunk_count}"
                    )

    latency_sec = time.time() - started
    return "".join(content_parts), {
        "usage": {},
        "latency_sec": latency_sec,
        "stream": True,
        "ttft_sec": (first_chunk_at - started) if first_chunk_at is not None else None,
        "chunk_count": chunk_count,
    }


def write_batch_csv(samples: list[dict], path: Path) -> None:
    fieldnames = list(samples[0].keys())
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(samples)


def load_samples(samples_path: Path) -> list[dict]:
    with samples_path.open() as handle:
        return [json.loads(line) for line in handle]


def run_batch(
    batch_index: int,
    batch_instance_ids: list[str],
    samples_by_id: dict[str, dict],
    output_root: Path,
    base_url: str,
    api_key: str,
    model: str,
    timeout: int,
    max_retries: int,
    retry_backoff_sec: float,
    max_workers: int,
    stream: bool,
) -> None:
    batch_dir = output_root / f"batch_{batch_index:02d}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    batch_samples = [samples_by_id[instance_id] for instance_id in batch_instance_ids]
    with (batch_dir / "samples.jsonl").open("w") as handle:
        for sample in batch_samples:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
    write_batch_csv(batch_samples, batch_dir / "samples.csv")

    def worker(sample: dict) -> dict:
        prompt = PROMPT_TEMPLATE.format(**sample)
        instance_id = sample["instance_id"]
        attempts = []
        raw_text = ""
        patch = ""
        for attempt in range(1, max_retries + 1):
            log(f"{instance_id}: start attempt {attempt}/{max_retries}")
            try:
                if stream:
                    raw_text, metadata = call_model_stream(
                        base_url=base_url,
                        api_key=api_key,
                        model=model,
                        prompt=prompt,
                        timeout=timeout,
                        instance_id=instance_id,
                    )
                else:
                    raw_text, metadata = call_model(base_url, api_key, model, prompt, timeout)
                patch = extract_patch(raw_text)
                attempts.append(
                    {
                        "attempt": attempt,
                        "status": "ok",
                        "latency_sec": metadata.get("latency_sec", 0.0),
                        "usage": metadata.get("usage", {}),
                    }
                )
                log(
                    f"{instance_id}: success on attempt {attempt} "
                    f"latency={metadata.get('latency_sec', 0.0):.2f}s "
                    f"diff_len={len(patch)}"
                )
                break
            except (TimeoutError, urllib.error.URLError) as exc:
                attempts.append(
                    {
                        "attempt": attempt,
                        "status": "retryable_error",
                        "error": str(exc),
                    }
                )
                log(f"{instance_id}: retryable error on attempt {attempt}: {exc}")
                if attempt == max_retries:
                    break
                time.sleep(retry_backoff_sec * attempt)
            except Exception as exc:
                attempts.append(
                    {
                        "attempt": attempt,
                        "status": "fatal_error",
                        "error": str(exc),
                    }
                )
                log(f"{instance_id}: fatal error on attempt {attempt}: {exc}")
                break

        (batch_dir / f"{instance_id}.raw.txt").write_text(raw_text)
        (batch_dir / f"{instance_id}.diff").write_text(patch)
        return {
            "instance_id": instance_id,
            "patch": patch,
            "prefix": model,
            "raw_len": len(raw_text),
            "diff_len": len(patch),
            "metadata": {
                "attempts": attempts,
                "final_status": attempts[-1]["status"] if attempts else "unknown",
            },
        }

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(batch_samples))) as executor:
        future_map = {executor.submit(worker, sample): sample["instance_id"] for sample in batch_samples}
        for future in concurrent.futures.as_completed(future_map):
            instance_id = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(
                    {
                        "instance_id": instance_id,
                        "patch": "",
                        "prefix": model,
                        "raw_len": 0,
                        "diff_len": 0,
                        "metadata": {"error": str(exc)},
                    }
                )

    results.sort(key=lambda item: batch_instance_ids.index(item["instance_id"]))
    with (batch_dir / "generation_summary.json").open("w") as handle:
        json.dump(results, handle, indent=2)
    with (batch_dir / "patches.json").open("w") as handle:
        json.dump(
            [
                {
                    "instance_id": item["instance_id"],
                    "patch": item["patch"],
                    "prefix": item["prefix"],
                }
                for item in results
            ],
            handle,
            indent=2,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a fixed GLM SWE-Bench Pro experiment.")
    parser.add_argument("--samples", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--batches", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="glm-5")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-backoff-sec", type=float, default=5.0)
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()

    samples_path = Path(args.samples)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    samples = load_samples(samples_path)
    samples_by_id = {sample["instance_id"]: sample for sample in samples}
    manifest = json.loads(Path(args.manifest).read_text())
    batches = json.loads(Path(args.batches).read_text())

    experiment_manifest = {
        "samples": str(samples_path),
        "instance_ids": manifest,
        "batches": batches,
        "model": args.model,
        "base_url": args.base_url,
        "timeout": args.timeout,
        "max_retries": args.max_retries,
        "retry_backoff_sec": args.retry_backoff_sec,
        "max_workers": args.max_workers,
        "stream": args.stream,
        "prompt_template": PROMPT_TEMPLATE,
    }
    (output_root / "experiment_manifest.json").write_text(json.dumps(experiment_manifest, indent=2))

    for batch_index, batch in enumerate(batches, start=1):
        log(f"batch_{batch_index:02d}: start with {len(batch)} instance(s)")
        run_batch(
            batch_index=batch_index,
            batch_instance_ids=batch,
            samples_by_id=samples_by_id,
            output_root=output_root,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            timeout=args.timeout,
            max_retries=args.max_retries,
            retry_backoff_sec=args.retry_backoff_sec,
            max_workers=args.max_workers,
            stream=args.stream,
        )
        log(f"batch_{batch_index:02d}: finished")


if __name__ == "__main__":
    main()
