"""Tests for actorion.py. No network: Discord is replaced by a fake opener."""
import copy
import io
import json
import re
import urllib.error
from pathlib import Path

import pytest
import yaml

import actorion

ROOT = Path(__file__).resolve().parent.parent
EVENT = json.loads((ROOT / "examples" / "push-event.json").read_text(encoding="utf-8"))
WEBHOOK = "https://discord.com/api/webhooks/123/secret-token"
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")


def settings(**overrides):
    base = {"webhook": WEBHOOK, "role": "", "max_commits": 15, "ignore_bots": False,
            "embed": True, "username": "", "dry_run": False}
    return {**base, **overrides}


def commit(n, name="Octo Cat", username="octocat", message=None):
    return {
        "id": f"{n:040d}",
        "message": message if message is not None else f"Commit {n}",
        "timestamp": "2026-11-07T13:43:27+01:00",
        "url": f"https://github.com/octo-org/example/commit/{n:040d}",
        "author": {"name": name, "username": username},
    }


def event_with(commits, **changes):
    e = copy.deepcopy(EVENT)
    e["commits"] = commits
    e.update(changes)
    return e


# ---- fake Discord ---------------------------------------------------------------------------------------

class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeDiscord:
    """Records requests; answers with the queued errors first, then 200."""

    def __init__(self, *errors):
        self.requests, self.errors = [], list(errors)

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.errors:
            code, body = self.errors.pop(0)
            if code == "network":
                raise urllib.error.URLError("Name or service not known")
            raise urllib.error.HTTPError(request.full_url, code, "error", {}, io.BytesIO(body.encode()))
        return FakeResponse()

    def payloads(self):
        return [json.loads(r.data) for r in self.requests]


# ---- settings ---------------------------------------------------------------------------------------------

def env(**values):
    base = {"ACTORION_WEBHOOK": WEBHOOK}
    base.update({f"ACTORION_{k.upper()}": v for k, v in values.items()})
    return base


def test_default_settings():
    assert actorion.read_settings(env()) == settings()


@pytest.mark.parametrize("value, expected", [("true", True), ("True", True), ("yes", True), ("1", True),
                                             ("false", False), ("", False), ("0", False), ("off", False)])
def test_boolean_inputs(value, expected):
    assert actorion.read_settings(env(ignore_bots=value))["ignore_bots"] is expected


@pytest.mark.parametrize("values, message", [
    ({"ACTORION_WEBHOOK": ""}, 'Input "webhook" is empty'),
    ({"ACTORION_WEBHOOK": "https://example.com/hook"}, "is not a Discord webhook URL"),
    ({"ACTORION_MAX_COMMITS": "0"}, '"max-commits" must be a number from 1 to 50'),
    ({"ACTORION_MAX_COMMITS": "51"}, '"max-commits" must be a number from 1 to 50'),
    ({"ACTORION_MAX_COMMITS": "ten"}, '"max-commits" must be a number from 1 to 50'),
    ({"ACTORION_ROLE": "@Media"}, '"role" must be a Discord role ID'),
    ({"ACTORION_EMBED": "maybe"}, 'Input "embed" must be true or false'),
])
def test_invalid_settings_are_explained(values, message):
    with pytest.raises(actorion.ActorionError, match=re.escape(message)):
        actorion.read_settings({**env(), **values})


def test_other_discord_domains_are_accepted():
    for host in ("discordapp.com", "ptb.discord.com", "canary.discord.com"):
        actorion.read_settings(env(webhook=f"https://{host}/api/webhooks/1/x"))


# ---- message ----------------------------------------------------------------------------------------------

def test_embed_message():
    payload = actorion.build_payload(EVENT, settings())
    [embed] = payload["embeds"]
    assert embed["title"] == "2 new commits on octo-org/example"
    assert embed["url"] == "https://github.com/octo-org/example/compare/1111111...3333333"
    assert embed["description"].splitlines() == [
        "[`2222222`](https://github.com/octo-org/example/commit/2222222222222222222222222222222222222222) "
        "Fix login redirect - *Octo Cat*",
        "[`3333333`](https://github.com/octo-org/example/commit/3333333333333333333333333333333333333333) "
        "Update README - *Octo Cat*",
    ]
    assert embed["footer"]["text"] == "GitHub · branch main · Nov 7, 2026, 12:43 UTC"
    assert embed["color"] == 0x24292F
    assert payload["allowed_mentions"] == {"parse": []}
    assert "content" not in payload


def test_text_message():
    payload = actorion.build_payload(EVENT, settings(embed=False))
    assert payload["content"] == ("**2 new commits on octo-org/example** (main)\n"
                                  "- Fix login redirect - *Octo Cat*\n- Update README - *Octo Cat*")
    assert "embeds" not in payload


def test_single_commit_is_singular():
    payload = actorion.build_payload(event_with([commit(1)]), settings())
    assert payload["embeds"][0]["title"] == "1 new commit on octo-org/example"


def test_long_pushes_are_summarised():
    payload = actorion.build_payload(event_with([commit(n) for n in range(1, 21)]), settings(max_commits=5))
    lines = payload["embeds"][0]["description"].splitlines()
    assert lines[0] == "...and 15 earlier commits"
    assert len(lines) == 6 and "Commit 20" in lines[-1]


def test_role_is_the_only_mention_allowed():
    payload = actorion.build_payload(event_with([commit(1, message="@everyone look")]), settings(role="500"))
    assert payload["content"] == "<@&500>"
    assert payload["allowed_mentions"] == {"parse": [], "roles": ["500"]}
    text = actorion.build_payload(event_with([commit(1)]), settings(role="500", embed=False))
    assert text["content"].startswith("<@&500>\n**1 new commit")


def test_username_override():
    assert actorion.build_payload(EVENT, settings(username="Commits"))["username"] == "Commits"
    assert "username" not in actorion.build_payload(EVENT, settings())


def test_bot_commits_can_be_ignored():
    commits = [commit(1, name="dependabot[bot]", username="dependabot[bot]"),
               commit(2, name="Renovate", username="renovate[bot]"), commit(3)]
    keep = actorion.build_payload(event_with(commits), settings())
    assert keep["embeds"][0]["title"].startswith("3 new commits")
    skip = actorion.build_payload(event_with(commits), settings(ignore_bots=True))
    assert skip["embeds"][0]["title"].startswith("1 new commit ")
    assert actorion.build_payload(event_with(commits[:2]), settings(ignore_bots=True)) is None


@pytest.mark.parametrize("changes", [
    {"deleted": True},         # branch deleted
    {"commits": []},           # tag push / nothing new
])
def test_nothing_to_post(changes):
    event = copy.deepcopy(EVENT)
    event.update(changes)
    assert actorion.build_payload(event, settings()) is None


def test_empty_commit_message_and_missing_author():
    c = commit(1, message="")
    c["author"] = None
    line = actorion.build_payload(event_with([c]), settings(embed=False))["content"].splitlines()[1]
    assert line == "- (no commit message) - *unknown*"


def test_tag_and_other_refs():
    assert actorion.branch_name("refs/tags/v1.0.0") == "v1.0.0"
    assert actorion.branch_name("refs/heads/feature/x") == "feature/x"


@pytest.mark.parametrize("value, expected", [
    ("2026-11-07T13:43:27+01:00", "Nov 7, 2026, 12:43 UTC"),
    ("2026-11-07T12:43:27Z", "Nov 7, 2026, 12:43 UTC"),
    ("2026-01-02T03:04:05", "Jan 2, 2026, 03:04 UTC"),
    ("not a date", ""),
    (None, ""),
])
def test_dates_are_english_utc(value, expected):
    assert actorion.english_date(value) == expected


def test_messages_fit_discord_limits_and_have_no_emojis():
    huge = [commit(n, message="x" * 500) for n in range(50)]
    embed_payload = actorion.build_payload(event_with(huge), settings(max_commits=50))
    assert len(embed_payload["embeds"][0]["description"]) <= 4096
    text_payload = actorion.build_payload(event_with(huge), settings(max_commits=50, embed=False))
    assert len(text_payload["content"]) <= 2000
    assert not EMOJI.search(json.dumps(actorion.build_payload(EVENT, settings())))


# ---- sending ----------------------------------------------------------------------------------------------

def test_post_sends_json_with_wait():
    discord = FakeDiscord()
    actorion.post(WEBHOOK, {"content": "hi"}, opener=discord)
    [request] = discord.requests
    assert request.full_url == WEBHOOK + "?wait=true"
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"content": "hi"}


def test_post_keeps_existing_query_string():
    discord = FakeDiscord()
    actorion.post(WEBHOOK + "?thread_id=9", {"content": "hi"}, opener=discord)
    assert discord.requests[0].full_url == WEBHOOK + "?thread_id=9&wait=true"


def test_post_retries_when_rate_limited():
    discord, waits = FakeDiscord((429, '{"retry_after": 2.5}')), []
    actorion.post(WEBHOOK, {"content": "hi"}, opener=discord, sleep=waits.append)
    assert len(discord.requests) == 2 and waits == [2.5]


def test_post_gives_up_after_repeated_rate_limits():
    discord = FakeDiscord(*[(429, '{"retry_after": 99}')] * 3)
    waits = []
    with pytest.raises(actorion.ActorionError, match="HTTP 429"):
        actorion.post(WEBHOOK, {"content": "hi"}, opener=discord, sleep=waits.append)
    assert waits == [30, 30]  # waits are capped


@pytest.mark.parametrize("error, message", [
    ((404, '{"message": "Unknown Webhook"}'), "It may have been deleted"),
    ((401, "{}"), "It may have been deleted"),
    ((400, '{"embeds": ["bad"]}'), 'HTTP 400): {"embeds": ["bad"]}'),
    (("network", ""), "Could not reach Discord"),
])
def test_post_errors_are_explained(error, message):
    with pytest.raises(actorion.ActorionError, match=re.escape(message)):
        actorion.post(WEBHOOK, {"content": "hi"}, opener=FakeDiscord(error))


# ---- main ---------------------------------------------------------------------------------------------------

def run_main(tmp_path, event=EVENT, discord=None, **env_values):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    environ = {"GITHUB_EVENT_PATH": str(path), "GITHUB_EVENT_NAME": "push", **env(**env_values)}
    output = []
    code = actorion.main([], environ=environ, opener=discord or FakeDiscord(), sleep=lambda s: None,
                         out=output.append)
    return code, "\n".join(output)


def test_main_posts_the_push(tmp_path):
    discord = FakeDiscord()
    code, output = run_main(tmp_path, discord=discord)
    assert code == 0 and output == "Posted 2 commits to Discord."
    assert discord.payloads()[0]["embeds"][0]["title"] == "2 new commits on octo-org/example"


def test_main_dry_run_posts_nothing(tmp_path):
    discord = FakeDiscord()
    code, output = run_main(tmp_path, discord=discord, dry_run="true")
    assert code == 0 and output.startswith("Dry run: this message would be posted:")
    assert discord.requests == []


def test_main_with_nothing_to_post(tmp_path):
    discord = FakeDiscord()
    code, output = run_main(tmp_path, event=event_with([]), discord=discord)
    assert (code, output, discord.requests) == (0, "Nothing to post: no new commits in this push.", [])


def test_main_ignores_other_events(tmp_path):
    path = tmp_path / "event.json"
    path.write_text("{}", encoding="utf-8")
    output = []
    code = actorion.main([], environ={"GITHUB_EVENT_PATH": str(path), "GITHUB_EVENT_NAME": "issues",
                                      **env()}, out=output.append)
    assert code == 0 and "handles push and pull_request events" in output[0]


def test_main_reports_errors_as_annotations_without_leaking_the_webhook(tmp_path):
    code, output = run_main(tmp_path, discord=FakeDiscord((404, "{}")))
    assert code == 1
    assert output.startswith("::error title=Actorion::Discord rejected the webhook (HTTP 404)")
    assert "secret-token" not in output


def test_main_without_event_file(tmp_path):
    output = []
    code = actorion.main(["--event", str(tmp_path / "missing.json")], environ=env(), out=output.append)
    assert code == 1 and "Could not read the event file" in output[0]


def test_main_reads_event_from_argument(tmp_path):
    path = tmp_path / "custom.json"
    path.write_text(json.dumps(EVENT), encoding="utf-8")
    discord = FakeDiscord()
    code = actorion.main(["--event", str(path)], environ=env(), opener=discord, out=lambda *_: None)
    assert code == 0 and len(discord.requests) == 1


# ---- action.yml ------------------------------------------------------------------------------------------------

def test_action_inputs_match_the_script():
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    [step] = action["runs"]["steps"]
    assert action["runs"]["using"] == "composite"
    inputs = action["inputs"]
    assert inputs["webhook"]["required"] is True
    # every input is passed to the script as ACTORION_<NAME>, and only through env (never inline)
    for name in inputs:
        var = "ACTORION_" + name.upper().replace("-", "_")
        assert step["env"][var] == "${{ inputs.%s }}" % name
    assert "${{" not in step["run"]
    assert set(step["env"]) == {"ACTORION_" + n.upper().replace("-", "_") for n in inputs}


def test_action_defaults_are_valid_settings():
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    environ = {"ACTORION_" + n.upper().replace("-", "_"): str(i.get("default", "")) for n, i in action["inputs"].items()}
    environ["ACTORION_WEBHOOK"] = WEBHOOK
    assert actorion.read_settings(environ) == settings()


def test_example_workflow_uses_the_action():
    workflow = yaml.safe_load((ROOT / "examples" / "discord-notify.yml").read_text(encoding="utf-8"))
    [step] = workflow["jobs"]["notify"]["steps"]
    assert step["uses"] == "Bernardo2409/Actorion@v1"
    assert step["with"]["webhook"] == "${{ secrets.DISCORD_WEBHOOK }}"
