"""Command line for pomelli-webapi.

Installed as ``pomelli-cli``. Every command is a thin shell over the library, so
anything here is also one call away in code.

    pomelli-cli cookies                 what jar am I about to use
    pomelli-cli ping                    is the session alive
    pomelli-cli account                 business and account ids
    pomelli-cli templates               shot types and their ids
    pomelli-cli styles                  curated styles
    pomelli-cli render photo.jpg -p Studio -p Flatlay -o out
    pomelli-cli survey                  walk the read-only methods
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .client import PomelliClient
from .constants import ASPECT_IDS, METHODS, STYLES, TEMPLATE_IDS, TEMPLATE_IDS_EXTRA
from .exceptions import PomelliError
from .sage import check_session
from .utils.cookies import Cookies
from .utils.load_browser_cookies import load_browser_cookies
from .utils.logger import set_log_level


def _shared_flags(parser: argparse.ArgumentParser, *, late: bool = False) -> None:
    """The flags that are not tied to one subcommand.

    Added to the main parser and again to every subcommand, so both
    ``pomelli-cli --cookies jar.json ping`` and ``pomelli-cli ping --cookies
    jar.json`` work. On the subcommands the defaults are suppressed: a subparser
    that does not see the flag then leaves the attribute alone instead of
    resetting it to None, which is what would otherwise wipe out a value already
    read from before the subcommand.
    """
    default = argparse.SUPPRESS if late else None
    parser.add_argument("--cookies", metavar="PATH", default=default,
                        help="JSON jar: a list of cookie records, or {name: value}")
    parser.add_argument("--browser", action="store_true", default=default,
                        help="read the jar from the local browsers instead")
    parser.add_argument("--proxy", metavar="URL", default=default,
                        help="proxy URL; defaults to $POMELLI_PROXY")
    parser.add_argument("--no-proxy", action="store_true", default=default,
                        help="connect directly")
    parser.add_argument("-v", "--verbose", action="store_true", default=default,
                        help="debug logging")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pomelli-cli",
        description="Drive Google Pomelli over HTTP, with no browser in the loop.",
    )
    _shared_flags(parser)

    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        made = sub.add_parser(name, help=help_text)
        _shared_flags(made, late=True)
        return made

    command("cookies", "show the jar that would be used, and its problems")
    command("ping", "check that the session answers")
    command("account", "business id, account id and business list")
    command("limits", "raw GetLimitInfo")
    command("templates", "shot types and their ids")
    command("styles", "curated styles")

    items = command("items", "list the account's business items")
    items.add_argument("--page", type=int, default=1)
    items.add_argument("--size", type=int, default=20)

    render = command("render", "run one photo through the photoshoot")
    render.add_argument("image")
    render.add_argument("-p", "--pick", action="append", required=True,
                        help="a shot type or style name; repeat up to six times")
    render.add_argument("-o", "--out", default=None,
                        help="directory to save the renders into")
    render.add_argument("-a", "--aspect", default=None,
                        choices=sorted(ASPECT_IDS), help="default: square")
    render.add_argument("--timeout", type=float, default=900.0)
    render.add_argument("--json", action="store_true", help="print the report as JSON")

    survey = command("survey", "call the read-only methods with an empty body")
    survey.add_argument("--all", action="store_true",
                        help="include the methods that write (delete, publish, render)")
    survey.add_argument("--method", action="append", default=None,
                        help="sweep only this method; repeatable")
    survey.add_argument("--json", action="store_true")

    return parser


def load_jar(args, *, strict: bool = True) -> Cookies:
    if args.cookies:
        jar = Cookies.from_file(args.cookies)
    elif args.browser:
        jar = load_browser_cookies(verbose=args.verbose)
    else:
        jar = load_browser_cookies(verbose=args.verbose)
    if strict and not jar:
        sys.exit(
            "no cookies. Pass --cookies PATH, or install the browser extra with "
            "`pip install pomelli-webapi[browser]` so they can be read from a "
            "signed-in browser."
        )
    return jar


def client_for(args, jar: Cookies) -> PomelliClient:
    proxy = None if args.no_proxy else args.proxy
    return PomelliClient(jar, proxy, strict=False)


async def cmd_cookies(args) -> int:
    jar = load_jar(args, strict=False)
    print(f"records : {len(jar)}")
    print(f"names   : {', '.join(sorted(jar.names())) or 'nothing'}")
    problems = check_session(jar, proxy=None if args.no_proxy else args.proxy)
    if problems:
        print("problems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("problems: none")
    for scope in ("labssage-pa.clients6.google.com", "labs.google.com", "www.gstatic.com"):
        scoped = jar.scoped(scope)
        print(f"for {scope}: {', '.join(sorted(scoped)) or 'nothing'}")
    return 0


async def cmd_ping(args) -> int:
    jar = load_jar(args)
    async with client_for(args, jar) as client:
        print(client.describe())
        result = await client.ping()
    if result["ok"]:
        print("session : alive")
        print("limits  :", json.dumps(result["limits"], ensure_ascii=False)[:300])
        return 0
    print("session : dead")
    print("error   :", result["error"])
    return 1


async def cmd_account(args) -> int:
    jar = load_jar(args)
    async with client_for(args, jar) as client:
        account = await client.account()
    print(json.dumps(account.as_dict(), ensure_ascii=False, indent=2))
    return 0


async def cmd_limits(args) -> int:
    jar = load_jar(args)
    async with client_for(args, jar) as client:
        limits = await client.limits()
    print(json.dumps(limits, ensure_ascii=False, indent=2))
    return 0


async def cmd_templates(args) -> int:
    table = {**TEMPLATE_IDS, **TEMPLATE_IDS_EXTRA}
    width = max(len(name) for name in table)
    for name, tid in sorted(table.items(), key=lambda kv: kv[1]):
        print(f"{name:<{width}}  {tid}")
    print(f"\naspects: {', '.join(sorted(set(ASPECT_IDS)))}")
    return 0


async def cmd_styles(args) -> int:
    width = max(len(body["label"]) for body in STYLES.values())
    for sid, body in STYLES.items():
        print(f"{body['label']:<{width}}  {sid}  ({len(body['images'])} refs)")
    return 0


async def cmd_items(args) -> int:
    jar = load_jar(args)
    async with client_for(args, jar) as client:
        items = await client.business_items(page=args.page, size=args.size)
    for item in items:
        print(json.dumps(item.as_dict(), ensure_ascii=False))
    print(f"\n{len(items)} items")
    return 0


async def cmd_render(args) -> int:
    jar = load_jar(args)

    def say(message: str) -> None:
        print(message, flush=True)

    async with client_for(args, jar) as client:
        report = await client.photoshoot(
            args.image, args.pick, aspect=args.aspect, timeout=args.timeout,
            on_progress=say,
        )
        if args.out:
            written = report.save(args.out)
            for path in written:
                print(f"saved {path}")
        if args.json:
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        else:
            print(report)
    return 0 if report.ok else 1


async def cmd_survey(args) -> int:
    jar = load_jar(args)
    async with client_for(args, jar) as client:
        rows = await client.sage.survey(
            args.method or METHODS, readonly_only=not args.all,
        )
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for row in rows:
        mark = "ok " if row["ok"] else f"!{row.get('status')}"
        body = json.dumps(row.get("body"), ensure_ascii=False, default=str)
        print(f"{mark:<5} {row['method']:<48} {body[:90]}")
    answered = sum(1 for row in rows if row["ok"])
    print(f"\n{answered} of {len(rows)} answered")
    return 0


COMMANDS = {
    "cookies": cmd_cookies,
    "ping": cmd_ping,
    "account": cmd_account,
    "limits": cmd_limits,
    "templates": cmd_templates,
    "styles": cmd_styles,
    "items": cmd_items,
    "render": cmd_render,
    "survey": cmd_survey,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    set_log_level("DEBUG" if args.verbose else "WARNING")
    try:
        return asyncio.run(COMMANDS[args.command](args))
    except PomelliError as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
