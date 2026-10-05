from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .budget import Budget, BudgetExceeded
from .fal_client import FalClient, MissingApiKey, get_api_key
from .generate import GenerateParams, build_prompt, estimate, generate
from .project import Project, default_root, load, project_dir, save, scan_scenes


def _parser():
    parser = argparse.ArgumentParser(prog="scene-to-hero")
    sub = parser.add_subparsers(dest="command")
    init = sub.add_parser("init"); init.add_argument("name"); init.add_argument("--scenes", required=True); init.add_argument("--root")
    order = sub.add_parser("order"); order.add_argument("name"); order.add_argument("--root"); order.add_argument("--move", action="append", default=[]); order.add_argument("--importance", action="append", default=[]); order.add_argument("--final")
    gen = sub.add_parser("generate"); gen.add_argument("name"); gen.add_argument("--root"); group = gen.add_mutually_exclusive_group(); group.add_argument("--prompt"); group.add_argument("--prompt-file"); gen.add_argument("--duration", type=float, default=4.2); gen.add_argument("--seed", type=int); gen.add_argument("--budget", type=float, default=10.0); gen.add_argument("--yes", action="store_true"); gen.add_argument("--dry-run", action="store_true"); gen.add_argument("--no-replace-last-frame", action="store_true"); gen.add_argument("--hold-frames", type=int, default=0)
    for name in ("interview", "finish", "upscale", "review", "export"):
        sub.add_parser(name)
    return parser


def _project(args):
    directory = project_dir(args.name, args.root)
    return directory, load(directory / "project.json")


def _init(args):
    directory = project_dir(args.name, args.root)
    if directory.exists():
        raise ValueError("project already exists")
    source = Path(args.scenes)
    scenes_dir = directory / "scenes"
    scenes_dir.mkdir(parents=True)
    scenes = scan_scenes(source)
    for scene in scenes:
        shutil.copy2(source / scene.path, scenes_dir / scene.path)
        scene.path = f"scenes/{scene.path}"
    save(Project(args.name, scenes, {"subject": {}, "scenery": {}}), directory / "project.json")
    print(directory)
    return 0


def _order(args):
    directory, project = _project(args)
    by_id = {scene.id: scene for scene in project.scenes}
    for item in args.importance:
        scene_id, value = item.split("=", 1)
        by_id[scene_id].importance = int(value)
    for item in args.move:
        scene_id, value = item.split("=", 1)
        scene = by_id.pop(scene_id)
        ordered = sorted(project.scenes, key=lambda value: value.order)
        ordered.remove(scene); ordered.insert(int(value), scene); project.scenes = ordered
        for index, entry in enumerate(project.scenes): entry.order = index
    if args.final:
        if args.final not in by_id: raise ValueError("unknown scene id")
        for scene in project.scenes: scene.role = "final" if scene.id == args.final else "reference"
    project.validate()
    save(project, directory / "project.json")
    for scene in sorted(project.scenes, key=lambda item: item.order): print(f"{scene.order}: {scene.id} ({scene.role}, importance {scene.importance})")
    return 0


def _prompt(project, args):
    if args.prompt is not None: return args.prompt
    if args.prompt_file is not None: return Path(args.prompt_file).read_text(encoding="utf-8")
    subject = project.knowledge.get("subject", {}).get("description")
    scenery = project.knowledge.get("scenery", {}).get("description")
    if subject and scenery: return build_prompt(subject, scenery, has_marking=bool(project.knowledge.get("subject", {}).get("has_marking", False)))
    raise ValueError("prompt is missing and knowledge descriptions are incomplete")


def _generate(args):
    directory, project = _project(args)
    final = project.final_scene()
    if final is None: raise ValueError("final を order --final で指定してください")
    prompt = _prompt(project, args)
    params = GenerateParams(directory / final.path, prompt, directory, "hero", args.duration, args.seed,
                            replace_last_frame=not args.no_replace_last_frame, hold_frames=args.hold_frames)
    info = estimate(params); budget = Budget(directory / "logs" / "cost.jsonl", args.budget, batch=project.name)
    print(f"billed_seconds: {info['billed_seconds']}  unit price: ${info['unit_price_usd']:.6f}  est: ${info['est_usd']:.6f}  existing: ${budget.total():.6f}  limit: ${args.budget:.6f}")
    if args.dry_run: return 0
    if not args.yes and input("Proceed? [y/N] ").strip().lower() != "y": return 1
    client = FalClient(get_api_key())
    result = generate(params, budget, client)
    print(result["path"])
    return 0


def main(argv=None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        if args.command == "init": return _init(args)
        if args.command == "order": return _order(args)
        if args.command == "generate": return _generate(args)
        if args.command in {"interview", "finish", "upscale", "review", "export"}:
            print("not implemented yet", file=sys.stderr); return 2
        parser.print_help(); return 0
    except (ValueError, FileNotFoundError, MissingApiKey, BudgetExceeded, RuntimeError) as exc:
        print(str(exc), file=sys.stderr); return 3 if isinstance(exc, BudgetExceeded) else 1


if __name__ == "__main__":
    raise SystemExit(main())
