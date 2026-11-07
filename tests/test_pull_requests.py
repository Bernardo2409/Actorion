"""Tests for pull request messages."""
import copy
import json
from pathlib import Path

import pytest
import yaml

import actorion
from tests.test_actorion import EMOJI, WEBHOOK, FakeDiscord, env, settings

ROOT = Path(__file__).resolve().parent.parent
PR_EVENT = json.loads((ROOT / "examples" / "pull-request-event.json").read_text(encoding="utf-8"))


def pr_event(action="opened", **pr_changes):
    e = copy.deepcopy(PR_EVENT)
    e["action"] = action
    e["pull_request"].update({"merged": False, "merged_at": None, "closed_at": None, "state": "open"})
    e["pull_request"].update(pr_changes)
    return e


def build(event, **overrides):
    return actorion.build_payload(event, settings(**overrides), "pull_request")


# ---- which actions are announced ------------------------------------------------------------------------

@pytest.mark.parametrize("action, changes, title, color", [
    ("opened", {}, "Pull request opened: #42 Add login with GitHub", 0x238636),
    ("reopened", {}, "Pull request reopened: #42 Add login with GitHub", 0x238636),
    ("closed", {"merged": True, "merged_at": "2026-11-07T12:43:27Z"},
     "Pull request merged: #42 Add login with GitHub", 0x8957E5),
    ("closed", {"closed_at": "2026-11-07T12:43:27Z"}, "Pull request closed: #42 Add login with GitHub", 0xDA3633),
    ("ready_for_review", {}, "Pull request marked as ready for review: #42 Add login with GitHub", 0x238636),
    ("converted_to_draft", {"draft": True}, "Pull request converted to draft: #42 Add login with GitHub", 0x6E7681),
    ("opened", {"draft": True}, "Pull request opened: #42 Add login with GitHub", 0x6E7681),
    ("synchronize", {}, "Pull request updated with new commits: #42 Add login with GitHub", 0x238636),
])
def test_announced_actions(action, changes, title, color):
    [embed] = build(pr_event(action, **changes))["embeds"]
    assert embed["title"] == title
    assert embed["color"] == color


@pytest.mark.parametrize("action", ["labeled", "assigned", "edited", "review_requested", "auto_merge_enabled"])
def test_other_actions_post_nothing(action):
    assert build(pr_event(action)) is None


# ---- message content -----------------------------------------------------------------------------------

def test_merged_embed():
    [embed] = build(PR_EVENT)["embeds"]
    assert embed == {
        "title": "Pull request merged: #42 Add login with GitHub",
        "url": "https://github.com/octo-org/example/pull/42",
        "description": "`dev -> main` by *octocat*\n3 commits, 5 files changed, +120 -30",
        "color": 0x8957E5,
        "footer": {"text": "GitHub · octo-org/example · Nov 7, 2026, 12:43 UTC"},
    }


def test_text_message():
    payload = build(PR_EVENT, embed=False)
    assert payload["content"] == (
        "**Pull request merged: #42 Add login with GitHub**\n"
        "octo-org/example · dev -> main · by *octocat*\n"
        "3 commits, 5 files changed, +120 -30\n"
        "<https://github.com/octo-org/example/pull/42>"
    )


def test_singular_and_missing_sizes():
    one = build(pr_event(commits=1, changed_files=1, additions=1, deletions=0))["embeds"][0]["description"]
    assert one.endswith("1 commit, 1 file changed, +1 -0")
    e = pr_event()
    for key in ("commits", "changed_files", "additions", "deletions"):
        del e["pull_request"][key]
    assert build(e)["embeds"][0]["description"] == "`dev -> main` by *octocat*"


def test_role_mention_and_no_other_pings():
    payload = build(pr_event(title="@everyone urgent fix"), role="500")
    assert payload["content"] == "<@&500>"
    assert payload["allowed_mentions"] == {"parse": [], "roles": ["500"]}


def test_bot_pull_requests_can_be_ignored():
    dependabot = pr_event(user={"login": "dependabot[bot]", "type": "Bot"})
    assert build(dependabot) is not None
    assert build(dependabot, ignore_bots=True) is None
    assert build(pr_event(), ignore_bots=True) is not None


def test_long_titles_fit_and_no_emojis():
    payload = build(pr_event(title="x" * 400))
    assert len(payload["embeds"][0]["title"]) <= 256
    assert not EMOJI.search(json.dumps(build(PR_EVENT)))


def test_pull_request_target_is_handled_the_same_way():
    assert actorion.build_payload(PR_EVENT, settings(), "pull_request_target") == build(PR_EVENT)


# ---- main ------------------------------------------------------------------------------------------------------

def run(tmp_path, event, event_name="pull_request", discord=None, **values):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    environ = {"GITHUB_EVENT_PATH": str(path), "GITHUB_EVENT_NAME": event_name, **values}
    output = []
    code = actorion.main([], environ=environ, opener=discord or FakeDiscord(), out=output.append)
    return code, "\n".join(output)


def test_main_posts_a_pull_request(tmp_path):
    discord = FakeDiscord()
    code, output = run(tmp_path, PR_EVENT, discord=discord, **env())
    assert (code, output) == (0, "Posted pull request #42 (merged) to Discord.")
    assert discord.payloads()[0]["embeds"][0]["title"].startswith("Pull request merged")


def test_main_with_an_ignored_action(tmp_path):
    discord = FakeDiscord()
    code, output = run(tmp_path, pr_event("labeled"), discord=discord, **env())
    assert (code, output, discord.requests) == (0, "Nothing to post for this event.", [])


def test_pull_request_from_a_fork_without_the_secret_is_skipped(tmp_path):
    fork = pr_event(head={"ref": "patch-1", "repo": {"full_name": "someone/example"}})
    code, output = run(tmp_path, fork, ACTORION_WEBHOOK="")
    assert code == 0 and "pull requests from forks" in output


def test_missing_secret_on_a_normal_pull_request_is_an_error(tmp_path):
    code, output = run(tmp_path, pr_event(), ACTORION_WEBHOOK="")
    assert code == 1 and 'Input "webhook" is empty' in output


def test_event_name_can_be_given_on_the_command_line(tmp_path):
    path = tmp_path / "pr.json"
    path.write_text(json.dumps(PR_EVENT), encoding="utf-8")
    discord, output = FakeDiscord(), []
    code = actorion.main(["--event", str(path), "--event-name", "pull_request"], environ=env(),
                         opener=discord, out=output.append)
    assert code == 0 and discord.payloads()[0]["embeds"][0]["title"].startswith("Pull request merged")


# ---- example workflows ----------------------------------------------------------------------------------------

def test_example_workflow_covers_pushes_and_pull_requests():
    workflow = yaml.safe_load((ROOT / "examples" / "discord-notify.yml").read_text(encoding="utf-8"))
    triggers = workflow[True]  # YAML reads the key "on" as True
    assert "push" in triggers and "pull_request" in triggers
    assert set(triggers["pull_request"]["types"]) == {"opened", "reopened", "closed", "ready_for_review"}
    for step in (s for job in workflow["jobs"].values() for s in job["steps"]):
        assert step["uses"] == "Bernardo2409/Actorion@v1"
        assert step["with"]["webhook"].startswith("${{ secrets.")


def test_webhook_constant_is_a_discord_url():
    assert WEBHOOK.startswith(actorion.WEBHOOK_PREFIXES)
