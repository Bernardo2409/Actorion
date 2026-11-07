"""Actorion: post the commits of a GitHub push to a Discord channel through a webhook.

Runs as a step of a GitHub Actions job (see action.yml), reading the push event that GitHub
provides. It only uses the Python standard library, so nothing has to be installed.

It can also run on your own computer to try a message without GitHub Actions:

    ACTORION_WEBHOOK=https://discord.com/api/webhooks/... python actorion.py --event examples/push-event.json
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

COLOR = 0x24292F
DEFAULT_MAX_COMMITS = 15
WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
    "https://ptb.discord.com/api/webhooks/",
    "https://canary.discord.com/api/webhooks/",
)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MAX_ATTEMPTS = 3          # when Discord answers 429 (too many requests)
MAX_RETRY_WAIT = 30       # seconds


class ActorionError(Exception):
    """A problem the user can fix; the message is shown as the job's error."""


# ---- settings -----------------------------------------------------------------------------------------

def parse_bool(value, name):
    text = (value or "").strip().lower()
    if text in ("true", "yes", "1", "on"):
        return True
    if text in ("false", "no", "0", "off", ""):
        return False
    raise ActorionError(f'Input "{name}" must be true or false, not "{value}".')


def read_settings(environ):
    """Read and validate the action's inputs, passed as ACTORION_* environment variables."""
    webhook = environ.get("ACTORION_WEBHOOK", "").strip()
    if not webhook:
        raise ActorionError(
            'Input "webhook" is empty. Store the webhook URL as a repository secret named '
            "DISCORD_WEBHOOK and pass it with: webhook: ${{ secrets.DISCORD_WEBHOOK }}"
        )
    if not webhook.startswith(WEBHOOK_PREFIXES):
        raise ActorionError('Input "webhook" is not a Discord webhook URL (https://discord.com/api/webhooks/...).')

    raw_max = environ.get("ACTORION_MAX_COMMITS", "").strip() or str(DEFAULT_MAX_COMMITS)
    if not raw_max.isdigit() or not 1 <= int(raw_max) <= 50:
        raise ActorionError(f'Input "max-commits" must be a number from 1 to 50, not "{raw_max}".')

    role = environ.get("ACTORION_ROLE", "").strip()
    if role and not role.isdigit():
        raise ActorionError(f'Input "role" must be a Discord role ID (a number), not "{role}".')

    return {
        "webhook": webhook,
        "role": role,
        "max_commits": int(raw_max),
        "ignore_bots": parse_bool(environ.get("ACTORION_IGNORE_BOTS"), "ignore-bots"),
        "embed": parse_bool(environ.get("ACTORION_EMBED", "true"), "embed"),
        "username": environ.get("ACTORION_USERNAME", "").strip(),
        "dry_run": parse_bool(environ.get("ACTORION_DRY_RUN"), "dry-run"),
    }


# ---- message --------------------------------------------------------------------------------------------

def is_bot(commit):
    """Commits by apps such as Dependabot: the author's name or username ends in [bot]."""
    author = commit.get("author") or {}
    return (author.get("name") or "").endswith("[bot]") or (author.get("username") or "").endswith("[bot]")


def english_date(value):
    """ "2026-11-07T13:43:27+01:00" -> "Nov 7, 2026, 12:43 UTC" (always English, always UTC)."""
    try:
        moment = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return f"{MONTHS[moment.month - 1]} {moment.day}, {moment.year}, {moment:%H:%M} UTC"


def branch_name(ref):
    for prefix in ("refs/heads/", "refs/tags/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def first_line(message):
    return (message or "").splitlines()[0] if (message or "").strip() else "(no commit message)"


def commit_lines(commits, max_commits, with_links):
    """One line per commit, oldest first, newest at the bottom; older ones summarised."""
    shown = commits[-max_commits:]
    lines = []
    if len(commits) > len(shown):
        lines.append(f"...and {len(commits) - len(shown)} earlier commit{'s' if len(commits) - len(shown) != 1 else ''}")
    for c in shown:
        author = (c.get("author") or {}).get("name") or "unknown"
        title = first_line(c.get("message"))
        if with_links and c.get("url"):
            lines.append(f"[`{c.get('id', '')[:7]}`]({c['url']}) {title} - *{author}*")
        else:
            lines.append(f"- {title} - *{author}*")
    return lines


def truncate(text, limit):
    return text if len(text) <= limit else text[: limit - 3] + "..."


def commits_to_post(event, settings):
    """The push's commits that should be announced, oldest first."""
    if event.get("deleted"):
        return []  # a branch was deleted
    commits = list(event.get("commits") or [])
    if settings["ignore_bots"]:
        commits = [c for c in commits if not is_bot(c)]
    return commits


def build_payload(event, settings):
    """The webhook message for a push event, or None when there is nothing to post."""
    commits = commits_to_post(event, settings)
    if not commits:
        return None  # e.g. a deleted branch, a tag push, or only bot commits

    repo = (event.get("repository") or {}).get("full_name", "repository")
    branch = branch_name(event.get("ref", ""))
    count = len(commits)
    title = f"{count} new commit{'s' if count != 1 else ''} on {repo}"
    url = event.get("compare") or (event.get("repository") or {}).get("html_url")
    footer = " · ".join(p for p in ("GitHub", f"branch {branch}" if branch else "",
                                     english_date(commits[-1].get("timestamp"))) if p)

    payload = {"allowed_mentions": {"parse": []}}  # nothing in a commit message can ping anyone
    if settings["username"]:
        payload["username"] = settings["username"][:80]

    if settings["embed"]:
        embed = {
            "title": truncate(title, 256),
            "description": truncate("\n".join(commit_lines(commits, settings["max_commits"], True)), 4096),
            "color": COLOR,
            "footer": {"text": truncate(footer, 2048)},
        }
        if url:
            embed["url"] = url
        payload["embeds"] = [embed]
        content = ""
    else:
        content = "\n".join([f"**{title}** ({branch})"] + commit_lines(commits, settings["max_commits"], False))

    if settings["role"]:
        # Only this role may be pinged.
        content = f"<@&{settings['role']}>" + (f"\n{content}" if content else "")
        payload["allowed_mentions"] = {"parse": [], "roles": [settings["role"]]}
    if content:
        payload["content"] = truncate(content, 2000)
    return payload


# ---- sending ----------------------------------------------------------------------------------------------

def post(webhook, payload, opener=urllib.request.urlopen, sleep=time.sleep):
    """Send the message. Retries when Discord asks to slow down (HTTP 429)."""
    url = webhook + ("&" if "?" in webhook else "?") + "wait=true"  # wait=true: Discord reports errors
    data = json.dumps(payload).encode("utf-8")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json",
            "User-Agent": "Actorion (GitHub Action)",
        })
        try:
            with opener(request, timeout=15) as response:
                return response.status
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 429 and attempt < MAX_ATTEMPTS:
                try:
                    wait = float(json.loads(body).get("retry_after", 1))
                except (ValueError, AttributeError):
                    wait = 1.0
                sleep(min(max(wait, 0.5), MAX_RETRY_WAIT))
                continue
            if e.code in (401, 404):
                raise ActorionError(f"Discord rejected the webhook (HTTP {e.code}). It may have been deleted: "
                                    "create a new one and update the DISCORD_WEBHOOK secret.") from None
            raise ActorionError(f"Discord rejected the message (HTTP {e.code}): {body[:300]}") from None
        except urllib.error.URLError as e:
            raise ActorionError(f"Could not reach Discord: {e.reason}") from None
    raise ActorionError("Discord kept asking to slow down; the message was not sent.")


# ---- entry point ---------------------------------------------------------------------------------------------

def main(argv=None, environ=None, opener=urllib.request.urlopen, sleep=time.sleep, out=print):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description="Post the commits of a GitHub push to Discord.")
    parser.add_argument("--event", default=environ.get("GITHUB_EVENT_PATH"),
                        help="path to a push event JSON file (default: the one GitHub Actions provides)")
    args = parser.parse_args(argv)

    try:
        event_name = environ.get("GITHUB_EVENT_NAME", "push")
        if event_name != "push":
            out(f"::notice::Actorion only handles push events; this run was triggered by '{event_name}'.")
            return 0
        if not args.event:
            raise ActorionError("No event to read. Run inside GitHub Actions, or pass --event <file>.")
        try:
            with open(args.event, encoding="utf-8") as f:
                event = json.load(f)
        except (OSError, ValueError) as e:
            raise ActorionError(f"Could not read the event file: {e}") from None

        settings = read_settings(environ)
        payload = build_payload(event, settings)
        if payload is None:
            out("Nothing to post: no new commits in this push.")
            return 0
        if settings["dry_run"]:
            out("Dry run: this message would be posted:")
            out(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        post(settings["webhook"], payload, opener=opener, sleep=sleep)
        count = len(commits_to_post(event, settings))
        out(f"Posted {count} commit{'s' if count != 1 else ''} to Discord.")
        return 0
    except ActorionError as e:
        out(f"::error title=Actorion::{e}")  # shown as an annotation on the GitHub Actions run
        return 1


if __name__ == "__main__":
    sys.exit(main())
