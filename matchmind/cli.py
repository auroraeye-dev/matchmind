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


def cmd_eval(args: argparse.Namespace) -> None:
    from matchmind.agents.vision import auto_event_stream, evaluate
    from matchmind.sim.tracking import tracking_feed

    totals: dict[str, list[int]] = {}
    exact = 0
    for seed in args.seeds:
        meta, truth = generate_match(seed)
        r = evaluate(truth, list(auto_event_stream(meta, tracking_feed(truth, seed), model=not args.rules)))
        for k, v in r.items():
            if "matched" in v:
                t = totals.setdefault(k, [0, 0, 0])
                t[0] += v["truth"]
                t[1] += v["detected"]
                t[2] += v["matched"]
        st, sd = r["score"]["truth"], r["score"]["detected"]
        exact += all(sd.get(k, 0) == v for k, v in st.items()) and sum(sd.values()) == sum(st.values())
    print(f"Auto-eventing from 10 Hz tracking over {len(args.seeds)} matches ({'rules' if args.rules else 'trained model'})")
    for k, (t, d, m) in totals.items():
        print(f"  {k:13s} recall {m / t:6.1%}  precision {m / d:6.1%}   (truth {t}, detected {d})")
    print(f"  final score exactly right in {exact}/{len(args.seeds)} matches")


def cmd_train_vision(args: argparse.Namespace) -> None:
    from matchmind.agents.vision_train import train

    m = train(list(range(args.train_from, args.train_from + args.train_n)), list(range(args.val_from, args.val_from + 10)))
    print(json.dumps({k: m[k] for k in ("trained_on", "validation", "threshold")}, indent=2))


def cmd_report(args: argparse.Namespace) -> None:
    from matchmind.copilot_reporter import publish_gist, save_report, write_report

    path = save_report(args.seed, asyncio.run(write_report(args.seed, model=args.model)))
    print(f"report written to {path}")
    if args.gist:
        print("gist:", publish_gist(path, public=args.public))


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
    e = sub.add_parser("eval-autoevent", help="score the Vision agent against ground truth")
    e.add_argument("--seeds", type=int, nargs="+", default=list(range(1, 21)))
    e.add_argument("--rules", action="store_true", help="use the hand-written rules instead of the trained model")
    e.set_defaults(func=cmd_eval)
    tv = sub.add_parser("train-vision", help="train the Vision agent's shot classifier on synthetic tracking")
    tv.add_argument("--train-from", type=int, default=101)
    tv.add_argument("--train-n", type=int, default=40)
    tv.add_argument("--val-from", type=int, default=201)
    tv.set_defaults(func=cmd_train_vision)
    rp = sub.add_parser("report", help="GitHub Copilot SDK agent writes a post-match report via the MatchMind MCP tools")
    rp.add_argument("--seed", type=int, default=7)
    rp.add_argument("--model", default=None)
    rp.add_argument("--gist", action="store_true", help="publish with the GitHub CLI (gh gist create)")
    rp.add_argument("--public", action="store_true")
    rp.set_defaults(func=cmd_report)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
