"""ripplelens command line."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__


def _add_diff_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=".", help="repository path (default: current directory)")
    p.add_argument(
        "--base",
        default=None,
        help="base ref; compared at merge-base(base, head). Default: HEAD when the tree is dirty, else HEAD~1",
    )
    p.add_argument("--head", default=None, help="head ref (default: working tree)")
    p.add_argument("--depth", type=int, default=3, help="max caller depth for signature/removed changes (default 3)")
    p.add_argument("--body-depth", type=int, default=2, help="max caller depth for body-only changes (default 2)")
    p.add_argument("--max-nodes", type=int, default=400, help="node budget before truncation (default 400)")
    p.add_argument("--timeout", type=float, default=60.0, help="per-request timeout in seconds (default 60)")
    p.add_argument("--no-base-server", action="store_true", help="do not start a server on the base revision")
    p.add_argument("--no-diagnostics", action="store_true", help="skip compile-error check at impacted sites")


def _options(args: argparse.Namespace):
    from .analysis.impact import Options

    return Options(
        max_depth=args.depth,
        body_depth=args.body_depth,
        max_nodes=args.max_nodes,
        request_timeout=args.timeout,
        base_server=not args.no_base_server,
        diagnostics=not args.no_diagnostics,
    )


def cmd_analyze(args: argparse.Namespace) -> int:
    from .analysis.pipeline import analyze
    from .analysis.report import review_context, to_markdown

    say = (lambda m: print(f"ripplelens: {m}", file=sys.stderr)) if not args.quiet else (lambda m: None)
    rep = analyze(args.repo, args.base, args.head, _options(args), progress=say)
    if args.json:
        Path(args.json).write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        say(f"wrote {args.json}")
    if args.format == "json":
        sys.stdout.write(json.dumps(rep, indent=2, ensure_ascii=False) + "\n")
    elif args.format == "context":
        sys.stdout.write(review_context(rep, args.budget) + "\n")
    else:
        sys.stdout.write(to_markdown(rep) + "\n")
    return 2 if args.fail_on_breaking and rep["summary"]["breaking"] else 0


def cmd_review(args: argparse.Namespace) -> int:
    from .analysis.pipeline import analyze
    from .http_server import serve

    say = lambda m: print(f"ripplelens: {m}", file=sys.stderr)
    rep = analyze(args.repo, args.base, args.head, _options(args), progress=say)
    serve(rep, port=args.port, open_browser=not args.no_open, block=True)
    return 0


def cmd_view(args: argparse.Namespace) -> int:
    from .http_server import serve

    rep = json.loads(Path(args.file).read_text(encoding="utf-8"))
    serve(rep, port=args.port, open_browser=not args.no_open, block=True)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import run_doctor

    return run_doctor(args.repo, as_json=args.json, start=not args.no_start)


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import main as mcp_main

    return mcp_main(args.repo)


def cmd_hook(args: argparse.Namespace) -> int:
    """Agent hooks. Must be fast and never block the agent: always exit 0."""
    import json
    import os

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        payload = {}
    try:
        from .git.diff import repo_root

        tool_input = payload.get("tool_input") or {}
        target = tool_input.get("file_path") or tool_input.get("path") or ""
        start = os.path.dirname(target) if target else payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or "."
        root = repo_root(start)
    except Exception:
        return 0
    if args.event == "post-edit":
        from .mcp_server import dirty_marker

        marker = dirty_marker(root)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(target), encoding="utf-8")
        return 0
    if args.event == "stop" and os.environ.get("RIPPLELENS_STOP_SUMMARY") == "1":
        from .analysis.impact import Options
        from .analysis.pipeline import analyze

        try:
            rep = analyze(root, None, None, Options(diagnostics=True), progress=lambda m: None)
        except Exception:
            return 0
        s = rep["summary"]
        if s["entities_changed"]:
            msg = (
                f"ripplelens: {s['entities_changed']} changed, {s['impacted']} impacted in {s['impacted_files']} files, "
                f"{s['breaking']} with compile errors"
            )
            print(json.dumps({"systemMessage": msg}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ripplelens",
        description="Function-level blast radius of a git diff, computed by compiler-backed language servers.",
    )
    ap.add_argument("--version", action="version", version=f"ripplelens {__version__}")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("analyze", help="print the impact report (markdown, json, or AI review context)")
    _add_diff_args(p)
    p.add_argument("--format", choices=["markdown", "json", "context"], default="markdown")
    p.add_argument("--json", metavar="FILE", help="also write impact.json to FILE")
    p.add_argument("--budget", type=int, default=4000, help="token budget for --format context")
    p.add_argument("--fail-on-breaking", action="store_true", help="exit 2 when compile errors are found at impacted sites")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("review", help="analyze and open the review UI in the browser")
    _add_diff_args(p)
    p.add_argument("--port", type=int, default=0, help="HTTP port (default: random free port)")
    p.add_argument("--no-open", action="store_true", help="do not open a browser")
    p.set_defaults(fn=cmd_review)

    p = sub.add_parser("view", help="open the review UI for an existing impact.json")
    p.add_argument("file")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(fn=cmd_view)

    p = sub.add_parser("doctor", help="check language servers and project configuration")
    p.add_argument("--repo", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-start", action="store_true", help="only look for executables; do not start servers")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("mcp", help="run the MCP server on stdio (keeps language servers warm)")
    p.add_argument("--repo", default=".")
    p.set_defaults(fn=cmd_mcp)

    p = sub.add_parser("hook", help="agent hook entry point (reads the hook JSON on stdin)")
    p.add_argument("event", choices=["post-edit", "stop"])
    p.set_defaults(fn=cmd_hook)

    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        from .git.diff import GitError

        if isinstance(e, GitError):
            print(f"ripplelens: {e}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
