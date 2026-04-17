"""Pytest output parser.

Runs *inside* the container. Reads stdout + stderr, emits output.json with
schema: {"tests": [{"name": "...", "status": "PASSED|FAILED|SKIPPED|ERROR"}, ...]}

Supports two modes:
  1. JUnit XML (preferred) — if /workspace/junit.xml exists, parse it.
  2. Pytest text output — regex-parse the short summary block.

We also accept the rich --json-report plugin output if present.
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple


STATUS_PASSED = "PASSED"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"
STATUS_ERROR = "ERROR"


# Pytest short-line summary pattern, e.g.:
#   PASSED tests/test_foo.py::test_bar
#   FAILED tests/test_foo.py::test_baz - AssertionError
#   tests/test_foo.py::test_bar PASSED                [ 50%]
LINE_RE = re.compile(
    r"^(?P<status>PASSED|FAILED|ERROR|SKIPPED)\s+(?P<name>\S+)"
    r"|^(?P<name2>\S+)\s+(?P<status2>PASSED|FAILED|ERROR|SKIPPED)"
)


def parse_junit(xml_path: Path) -> List[Tuple[str, str]]:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    results: List[Tuple[str, str]] = []
    # support both <testsuite> top-level and <testsuites> wrapper
    suites = root.findall(".//testsuite") or [root]
    for suite in suites:
        for case in suite.findall("testcase"):
            classname = case.get("classname", "")
            name = case.get("name", "")
            full = f"{classname}::{name}" if classname else name
            if case.find("failure") is not None:
                results.append((full, STATUS_FAILED))
            elif case.find("error") is not None:
                results.append((full, STATUS_ERROR))
            elif case.find("skipped") is not None:
                results.append((full, STATUS_SKIPPED))
            else:
                results.append((full, STATUS_PASSED))
    return results


def parse_json_report(path: Path) -> List[Tuple[str, str]]:
    data = json.loads(path.read_text())
    results: List[Tuple[str, str]] = []
    for t in data.get("tests", []):
        nodeid = t.get("nodeid", "")
        outcome = t.get("outcome", "").upper()
        status_map = {
            "PASSED": STATUS_PASSED,
            "FAILED": STATUS_FAILED,
            "ERROR": STATUS_ERROR,
            "SKIPPED": STATUS_SKIPPED,
        }
        results.append((nodeid, status_map.get(outcome, STATUS_ERROR)))
    return results


def parse_stdout_text(stdout: str) -> List[Tuple[str, str]]:
    results: List[Tuple[str, str]] = []
    for line in stdout.splitlines():
        line = line.strip()
        m = LINE_RE.match(line)
        if not m:
            continue
        name = m.group("name") or m.group("name2")
        status = m.group("status") or m.group("status2")
        if name and status and "::" in name:
            results.append((name, status))
    # Dedup preserving order
    seen = set()
    uniq: List[Tuple[str, str]] = []
    for n, s in results:
        if n in seen:
            continue
        seen.add(n)
        uniq.append((n, s))
    return uniq


def main(stdout_path: Path, stderr_path: Path, output_path: Path) -> None:
    stdout = stdout_path.read_text(errors="ignore") if stdout_path.exists() else ""
    stderr = stderr_path.read_text(errors="ignore") if stderr_path.exists() else ""

    results: List[Tuple[str, str]] = []

    junit = Path("/workspace/junit.xml")
    json_report = Path("/workspace/pytest-report.json")
    if junit.exists():
        try:
            results = parse_junit(junit)
        except Exception:
            results = []
    if not results and json_report.exists():
        try:
            results = parse_json_report(json_report)
        except Exception:
            results = []
    if not results:
        results = parse_stdout_text(stdout + "\n" + stderr)

    payload = {"tests": [{"name": n, "status": s} for n, s in results]}
    output_path.write_text(json.dumps(payload, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: parser.py <stdout> <stderr> <output.json>")
        sys.exit(2)
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
