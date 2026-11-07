"""Actorion: post GitHub pushes and pull requests to a Discord channel through a webhook.

Runs as a step of a GitHub Actions job (see action.yml), reading the push or pull_request event
that GitHub provides. It only uses the Python standard library, so nothing has to be installed.

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


def finish_payload(settings, embed=None, content=""):
    """Add the options shared by every message: author name, role mention and allowed mentions."""
    payload = {"allowed_mentions": {"parse": []}}  # nothing in a title or message can ping anyone
    if settings["username"]:
        payload["username"] = settings["username"][:80]
    if embed is not None:
        payload["embeds"] = [embed]
    if settings["role"]:
        # Only this role may be pinged.
        content = f"<@&{settings['role']}>" + (f"\n{content}" if content else "")
        payload["allowed_mentions"] = {"parse": [], "roles": [settings["role"]]}
    if content:
        payload["content"] = truncate(content, 2000)
    return payload


def build_push_payload(event, settings):
    """The message for a push, or None when there is nothing to post."""
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

    if not settings["embed"]:
        return finish_payload(settings, content="\n".join(
            [f"**{title}** ({branch})"] + commit_lines(commits, settings["max_commits"], False)))
    embed = {
        "title": truncate(title, 256),
        "description": truncate("\n".join(commit_lines(commits, settings["max_commits"], True)), 4096),
        "color": COLOR,
        "footer": {"text": truncate(footer, 2048)},
    }
    if url:
        embed["url"] = url
    return finish_payload(settings, embed=embed)


# Pull request actions that are announced, and the verb used for each. Others are ignored.
PR_COLORS = {"open": 0x238636, "merged": 0x8957E5, "closed": 0xDA3633, "draft": 0x6E7681}
PR_ACTIONS = {
    "opened": "opened",
    "reopened": "reopened",
    "closed": "closed",               # becomes "merged" when the pull request was merged
    "ready_for_review": "marked as ready for review",
    "converted_to_draft": "converted to draft",
    "synchronize": "updated with new commits",
}


def is_bot_user(user):
    user = user or {}
    return user.get("type") == "Bot" or (user.get("login") or "").endswith("[bot]")


def pr_state(event):
    """(verb, color key) for a pull request event, or None when this action isn't announced."""
    action = event.get("action")
    pr = event.get("pull_request") or {}
    if action not in PR_ACTIONS:
        return None
    if action == "closed":
        return ("merged", "merged") if pr.get("merged") else ("closed", "closed")
    if action == "converted_to_draft" or pr.get("draft"):
        return PR_ACTIONS[action], "draft"
    return PR_ACTIONS[action], "open"


def build_pr_payload(event, settings):
    """The message for a pull request event, or None when there is nothing to post."""
    pr = event.get("pull_request") or {}
    state = pr_state(event)
    if state is None or not pr:
        return None
    if settings["ignore_bots"] and is_bot_user(pr.get("user")):
        return None  # e.g. Dependabot pull requests
    verb, color = state

    repo = (pr.get("base", {}).get("repo") or event.get("repository") or {}).get("full_name", "repository")
    number, title = pr.get("number"), first_line(pr.get("title"))
    author = (pr.get("user") or {}).get("login") or "unknown"
    branches = f"{(pr.get('head') or {}).get('ref', '?')} -> {(pr.get('base') or {}).get('ref', '?')}"
    when = pr.get("merged_at") or pr.get("closed_at") or pr.get("updated_at") or pr.get("created_at")
    headline = f"Pull request {verb}: #{number} {title}"

    size = []
    if isinstance(pr.get("commits"), int):
        size.append(f"{pr['commits']} commit{'s' if pr['commits'] != 1 else ''}")
    if isinstance(pr.get("changed_files"), int):
        size.append(f"{pr['changed_files']} file{'s' if pr['changed_files'] != 1 else ''} changed")
    if isinstance(pr.get("additions"), int) and isinstance(pr.get("deletions"), int):
        size.append(f"+{pr['additions']} -{pr['deletions']}")

    if not settings["embed"]:
        lines = [f"**{headline}**", f"{repo} · {branches} · by *{author}*"]
        if size:
            lines.append(", ".join(size))
        if pr.get("html_url"):
            lines.append(f"<{pr['html_url']}>")  # <...> hides Discord's link preview
        return finish_payload(settings, content="\n".join(lines))

    description = [f"`{branches}` by *{author}*"]
    if size:
        description.append(", ".join(size))
    embed = {
        "title": truncate(headline, 256),
        "description": truncate("\n".join(description), 4096),
        "color": PR_COLORS[color],
        "footer": {"text": truncate(" · ".join(p for p in ("GitHub", repo, english_date(when)) if p), 2048)},
    }
    if pr.get("html_url"):
        embed["url"] = pr["html_url"]
    return finish_payload(settings, embed=embed)


PULL_REQUEST_EVENTS = ("pull_request", "pull_request_target")
SUPPORTED_EVENTS = ("push",) + PULL_REQUEST_EVENTS


def build_payload(event, settings, event_name="push"):
    """The webhook message for a push or pull request event, or None when there is nothing to post."""
    if event_name in PULL_REQUEST_EVENTS:
        return build_pr_payload(event, settings)
    return build_push_payload(event, settings)


def is_fork_pull_request(event):
    pr = event.get("pull_request") or {}
    head = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    base = ((pr.get("base") or {}).get("repo") or {}).get("full_name")
    return bool(head and base and head != base)


def summary(event, settings, event_name):
    """What was posted, for the job log."""
    if event_name in PULL_REQUEST_EVENTS:
        verb, _ = pr_state(event)
        return f"Posted pull request #{(event.get('pull_request') or {}).get('number')} ({verb}) to Discord."
    count = len(commits_to_post(event, settings))
    return f"Posted {count} commit{'s' if count != 1 else ''} to Discord."


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
    parser = argparse.ArgumentParser(description="Post a GitHub push or pull request to Discord.")
    parser.add_argument("--event", default=environ.get("GITHUB_EVENT_PATH"),
                        help="path to an event JSON file (default: the one GitHub Actions provides)")
    parser.add_argument("--event-name", default=environ.get("GITHUB_EVENT_NAME", "push"),
                        help="push or pull_request (default: the event that started the workflow)")
    args = parser.parse_args(argv)

    try:
        event_name = args.event_name
        if event_name not in SUPPORTED_EVENTS:
            out(f"::notice::Actorion handles push and pull_request events; this run was triggered by '{event_name}'.")
            return 0
        if not args.event:
            raise ActorionError("No event to read. Run inside GitHub Actions, or pass --event <file>.")
        try:
            with open(args.event, encoding="utf-8") as f:
                event = json.load(f)
        except (OSError, ValueError) as e:
            raise ActorionError(f"Could not read the event file: {e}") from None

        if (event_name in PULL_REQUEST_EVENTS and not environ.get("ACTORION_WEBHOOK", "").strip()
                and is_fork_pull_request(event)):
            # GitHub doesn't pass secrets to workflows of pull requests from forks.
            out("::notice::Skipped: GitHub doesn't give secrets (the webhook) to pull requests from forks.")
            return 0
        settings = read_settings(environ)
        payload = build_payload(event, settings, event_name)
        if payload is None:
            out("Nothing to post for this event." if event_name in PULL_REQUEST_EVENTS
                else "Nothing to post: no new commits in this push.")
            return 0
        if settings["dry_run"]:
            out("Dry run: this message would be posted:")
            out(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        post(settings["webhook"], payload, opener=opener, sleep=sleep)
        out(summary(event, settings, event_name))
        return 0
    except ActorionError as e:
        out(f"::error title=Actorion::{e}")  # shown as an annotation on the GitHub Actions run
        return 1


if __name__ == "__main__":
    sys.exit(main())
