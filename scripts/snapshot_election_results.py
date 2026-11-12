"""Save every MP's seats and every seat's election results from Parliament's Members API, with the party that came
second in each, as a fallback copy shipped with the code.

The server fetches results itself, a few minutes' worth per update; if Parliament's service is unreachable from it
or answers empty, the pages would show no results at all. This copy fills any member or seat the server's own copy
lacks, and any runner-up the server has not looked up yet (so it never asks Parliament for those).

    uv run python scripts/snapshot_election_results.py data/raw/elections.json [--relist]

The seats are those in the server's copy given (its members' seats replace the saved ones; a missing file leaves the
saved ones as they are). The script starts from the copy already saved and can be stopped and run again: it lists the
seats the copy lacks, or whose results have no election id (saved before those were kept), then looks up the
runner-up of each result that has none, one request per result, saving every 100 requests. ``--relist`` lists every
seat afresh first (to pick up new elections), keeping the runner-ups already found; stopped part-way, it starts the
listing again on the next run. Requests go one at a time, with the module's pause between them; if Parliament asks
us to slow down, it saves and stops. A full run from nothing is about 5,000 requests (roughly 40 minutes).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from hansard.sources.demographics import USER_AGENT
from hansard.sources.elections import (
    REFRESH,
    SNAPSHOT,
    SOURCE,
    Throttled,
    fetch_results,
    fetch_runner_up,
    keep_runner_ups,
    needs_runner_up,
    saved_snapshot,
)


def save(reps: dict[str, Any], results: dict[str, Any]) -> None:
    tmp = SNAPSHOT.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fetched_at": datetime.now(UTC).isoformat(timespec="seconds"), "source": SOURCE,
                               "representations": {k: v for k, v in reps.items() if v}, "results": results},
                              sort_keys=True, separators=(",", ":")))
    tmp.replace(SNAPSHOT)


def main(cache: Path, relist: bool = False) -> None:
    saved = saved_snapshot()
    reps: dict[str, Any] = dict(saved.get("representations") or {})
    if cache.exists():
        reps.update({k: v for k, v in (json.loads(cache.read_text()).get("representations") or {}).items() if v})
    results: dict[str, list[dict[str, Any]]] = dict(saved.get("results") or {})
    seats = sorted({str(r["id"]) for rs in reps.values() for r in rs}, key=int)
    week_ago = (datetime.now(UTC) - REFRESH).isoformat()
    requests = 0

    def step() -> None:
        nonlocal requests
        requests += 1
        if requests % 100 == 0:
            save(reps, results)
            print(f"{requests} requests; {sum(1 for rs in results.values() for r in rs if r.get('runner_up'))} "
                  f"results with a runner-up", flush=True)

    try:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as client:
            for cid in seats:
                if relist or cid not in results or any(r.get("election_id") is None for r in results[cid]):
                    results[cid] = keep_runner_ups(fetch_results(client, int(cid)), results.get(cid, []))
                    step()
            stamp = datetime.now(UTC).isoformat(timespec="seconds")
            for cid in seats:
                for r in results.get(cid) or []:
                    if needs_runner_up(r, week_ago) and r.get("election_id") is not None:
                        r.update(fetch_runner_up(client, int(cid), int(r["election_id"]), checked=stamp))
                        step()
    except Throttled as exc:
        print(f"Parliament asked us to slow down ({exc}): saved what was fetched; run again later", flush=True)
    finally:
        save(reps, results)
    all_results = [r for rs in results.values() for r in rs]
    print(f"saved {len(results)} seats ({len(all_results)} results, "
          f"{sum(1 for r in all_results if r.get('runner_up'))} with a runner-up, "
          f"{sum(1 for r in all_results if needs_runner_up(r, week_ago))} still to look up) to {SNAPSHOT}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(Path(args[0] if args else "data/raw/elections.json"), relist="--relist" in sys.argv)
