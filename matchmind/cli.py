"""CLI: generate synthetic datasets or run the pipeline headless.

  python -m matchmind.cli generate --seeds 1 7 42 --out data/
  python -m matchmind.cli run --seed 7 --mode casual --team NBR --lang es
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from matchmind.orchestrator import Orchestrator
from matchmind.schema import Persona
from matchmind.sim.engine import generate_match


def cmd_generate(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for seed in args.seeds:
        meta, events = generate_match(seed)
        (out / f"match_{seed}.meta.json").write_text(meta.model_dump_json(indent=2))
        with (out / f"match_{seed}.events.jsonl").open("w") as f:
            for e in events:
                f.write(e.model_dump_json(exclude_none=True) + "\n")
        print(f"seed {seed}: {len(events)} events -> {out}/match_{seed}.events.jsonl")


def cmd_run(args: argparse.Namespace) -> None:
    meta, events = generate_match(args.seed)
    persona = Persona(id="cli", mode=args.mode, favorite_team_id=args.team, language=args.lang, metric=args.metric)

    async def publish(msg: dict) -> None:
        if msg["type"] == "overlay" and msg["overlay"]["kind"] != "player_tag":
            ov = msg["overlay"]
            print(f"[{ov['clock']:>8}] {ov['title']}\n           {ov['body']}")
        elif msg["type"] == "trace" and args.trace:
            print("    trace", json.dumps(msg["trace"]))

    async def main() -> None:
        orch = Orchestrator(meta, [persona], publish)
        for ev in events:
            await orch.on_event(ev)
            await asyncio.sleep(0)
        await orch.drain()

    asyncio.run(main())


def main() -> None:
    ap = argparse.ArgumentParser(prog="matchmind")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write synthetic match datasets")
    g.add_argument("--seeds", type=int, nargs="+", default=[7])
    g.add_argument("--out", default="data")
    g.set_defaults(func=cmd_generate)
    r = sub.add_parser("run", help="run the agent pipeline headless and print overlays")
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--mode", choices=["analyst", "casual"], default="analyst")
    r.add_argument("--team", default=None, help="favourite team id, e.g. NBR")
    r.add_argument("--lang", default="en")
    r.add_argument("--metric", default="xg")
    r.add_argument("--trace", action="store_true")
    r.set_defaults(func=cmd_run)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
