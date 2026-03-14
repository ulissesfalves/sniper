from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
OFFICIAL_SNAPSHOT_HOST = REPO_ROOT / "data" / "models" / "phase4" / "phase4_execution_snapshot.parquet"
TEMP_SNAPSHOT_HOST = REPO_ROOT / "data" / "models" / "phase4" / "phase4_execution_snapshot_test_nonzero_runtime.parquet"
TEMP_SNAPSHOT_CONTAINER = "/app/data/models/phase4/phase4_execution_snapshot_test_nonzero_runtime.parquet"
REDIS_KEYS = [
    "sniper:portfolio_targets:v1",
    "sniper:portfolio_status:v1",
    "sniper:portfolio_state:v1:bridge01:paper:stream_cursor",
    "sniper:portfolio_state:v1:sniper-paper-binance-spot-main:paper:last_revision_accepted",
    "sniper:portfolio_state:v1:sniper-paper-binance-spot-main:paper:last_accepted_target",
    "sniper:portfolio_state:v1:sniper-paper-binance-spot-main:paper:last_revision_applied",
    "sniper:portfolio_state:v1:sniper-paper-binance-spot-main:paper:last_applied_target",
    "sniper:portfolio_state:v1:sniper-paper-binance-spot-main:paper:deferred_target",
    "sniper:portfolio_revision:v1:sniper-paper-binance-spot-main:paper",
]
UNIT_TESTS = [
    "tests/unit/test_nautilus_bridge_acceptance.py",
    "tests/unit/test_nautilus_bridge_consumer.py",
    "tests/unit/test_nautilus_bridge_contract.py",
    "tests/unit/test_nautilus_bridge_phase4_publisher.py",
    "tests/unit/test_nautilus_bridge_reconciler.py",
]
RUNTIME_TESTS = [
    "/app/services/nautilus_bridge/tests_integration_312/test_paper_executor.py",
    "/app/services/nautilus_bridge/tests_integration_312/test_status_flow.py",
]


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("> " + " ".join(command))
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.stdout.strip():
        print(completed.stdout.strip())
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)
    if check and completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}")
    return completed


def compose(*args: str) -> list[str]:
    return ["docker", "compose", *args]


def official_snapshot_hash() -> str:
    return hashlib.sha256(OFFICIAL_SNAPSHOT_HOST.read_bytes()).hexdigest()


def build_bridge() -> None:
    run(compose("--profile", "paper", "build", "nautilus_bridge"))


def run_311_safe_unit_tests() -> None:
    run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{REPO_ROOT}:/workspace",
            "-w",
            "/workspace",
            "python:3.11-slim",
            "sh",
            "-lc",
            "pip install --quiet pytest redis && PYTHONPATH=/workspace pytest "
            + " ".join(UNIT_TESTS),
        ],
    )


def run_312_runtime_tests() -> None:
    run(
        compose(
            "--profile",
            "paper",
            "run",
            "--rm",
            "nautilus_bridge",
            "python",
            "-m",
            "pytest",
            *RUNTIME_TESTS,
        ),
    )


def ensure_services() -> None:
    run(compose("up", "-d", "redis"))
    run(compose("--profile", "paper", "up", "-d", "--force-recreate", "nautilus_bridge"))
    time.sleep(8)


def reset_redis_state() -> None:
    run(compose("exec", "-T", "redis", "redis-cli", "-n", "0", "DEL", *REDIS_KEYS))


def create_temp_snapshot() -> None:
    script = (
        "from pathlib import Path; import pandas as pd; "
        "src=Path('/app/data/models/phase4/phase4_execution_snapshot.parquet'); "
        f"dst=Path('{TEMP_SNAPSHOT_CONTAINER}'); "
        "df=pd.read_parquet(src).copy(); "
        "df.loc[df['symbol'].astype(str).str.upper()=='AAVE','position_usdt']=10000.0; "
        "df.loc[df['symbol'].astype(str).str.upper()=='ADA','position_usdt']=6000.0; "
        "df.to_parquet(dst, index=False); "
        "print(dst)"
    )
    run(compose("exec", "-T", "nautilus_bridge", "python", "-c", script))


def remove_temp_snapshot() -> None:
    TEMP_SNAPSHOT_HOST.unlink(missing_ok=True)


def publish_mock() -> None:
    run(compose("exec", "-T", "nautilus_bridge", "python", "-m", "services.nautilus_bridge.mock_publisher"))


def publish_phase4_official() -> None:
    run(compose("exec", "-T", "nautilus_bridge", "python", "-m", "services.nautilus_bridge.phase4_publisher"))


def publish_phase4_temp() -> None:
    run(
        compose(
            "exec",
            "-T",
            "nautilus_bridge",
            "sh",
            "-lc",
            (
                f"SNIPER_BRIDGE_PHASE4_SNAPSHOT={TEMP_SNAPSHOT_CONTAINER} "
                "python -m services.nautilus_bridge.phase4_publisher"
            ),
        ),
    )


def read_status_entries() -> list[dict[str, str]]:
    script = """
import json
import redis

r = redis.Redis.from_url("redis://redis:6379/0")
entries = r.xrange("sniper:portfolio_status:v1")
payload = []
for stream_id, fields in entries:
    item = {"_id": stream_id.decode() if isinstance(stream_id, bytes) else str(stream_id)}
    for key, value in fields.items():
        norm_key = key.decode() if isinstance(key, bytes) else str(key)
        norm_value = value.decode() if isinstance(value, bytes) else str(value)
        item[norm_key] = norm_value
    payload.append(item)
print(json.dumps(payload))
""".strip()
    completed = run(
        compose("exec", "-T", "nautilus_bridge", "python", "-c", script),
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return []
    return json.loads(lines[-1])


def bridge_logs() -> str:
    return run(compose("logs", "--tail", "200", "nautilus_bridge")).stdout


def wait_for_sequence(expected: list[str], *, timeout_secs: int = 60) -> list[dict[str, str]]:
    deadline = time.time() + timeout_secs
    last_entries: list[dict[str, str]] = []
    while time.time() < deadline:
        last_entries = read_status_entries()
        statuses = [entry.get("status", "") for entry in last_entries]
        if is_subsequence(statuses, expected):
            return last_entries
        time.sleep(2)
    raise RuntimeError(
        "Timed out waiting for status sequence "
        + " -> ".join(expected)
        + f". Last statuses: {[entry.get('status', '') for entry in last_entries]}"
    )


def is_subsequence(actual: list[str], expected: list[str]) -> bool:
    index = 0
    for status in actual:
        if index < len(expected) and status == expected[index]:
            index += 1
    return index == len(expected)


def run_smoke(name: str, publisher, expected: list[str]) -> list[dict[str, str]]:
    print(f"\n=== Smoke: {name} ===")
    reset_redis_state()
    ensure_services()
    publisher()
    entries = wait_for_sequence(expected)
    statuses = [entry["status"] for entry in entries]
    print(f"Statuses: {statuses}")
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bridge validation suite.")
    parser.add_argument("--skip-build", action="store_true", help="Skip docker compose build step.")
    args = parser.parse_args()

    original_hash = official_snapshot_hash()
    results: dict[str, list[dict[str, str]]] = {}
    try:
        if not args.skip_build:
            build_bridge()
        run_311_safe_unit_tests()
        run_312_runtime_tests()
        results["mock"] = run_smoke(
            "mock_publisher",
            publish_mock,
            ["received", "accepted", "submitted", "filled"],
        )
        results["phase4_official"] = run_smoke(
            "phase4_publisher_official",
            publish_phase4_official,
            ["received", "accepted", "noop_band"],
        )
        create_temp_snapshot()
        results["phase4_temp"] = run_smoke(
            "phase4_publisher_temp_nonzero",
            publish_phase4_temp,
            ["received", "accepted", "submitted", "filled"],
        )
    finally:
        remove_temp_snapshot()

    final_hash = official_snapshot_hash()
    if original_hash != final_hash:
        raise RuntimeError("Official phase4 snapshot hash changed unexpectedly")

    print("\n=== Summary ===")
    print(json.dumps({name: [entry["status"] for entry in entries] for name, entries in results.items()}, indent=2))
    print(f"official_snapshot_sha256={final_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
