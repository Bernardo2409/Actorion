# Actorion

Actorion is a GitHub Action that posts pushes and pull requests to a Discord channel through a webhook.

There is no bot to run and no server to keep online: GitHub runs the action on its own machines whenever you push, and the message arrives in Discord a few seconds later.

## Contents

- [How it works](#how-it-works)
- [Setup](#setup)
- [Choosing what is announced](#choosing-what-is-announced)
- [Inputs](#inputs)
- [Message format](#message-format)
- [Examples](#examples)
- [Trying it on your computer](#trying-it-on-your-computer)
- [Cost](#cost)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [License](#license)

## How it works

1. Something happens in a repository that has the Actorion workflow: a push, or a pull request is opened, merged, closed...
2. GitHub starts a temporary machine and runs the action, which reads that event.
3. The action sends one message to your Discord webhook URL.
4. Discord posts it in the channel the webhook belongs to, under the webhook's name and avatar.

Each push produces one message listing its commits, and each pull request event produces one message about that pull request. Deleted branches, tag pushes without commits, pull request events that aren't announced (such as labels) and events filtered out by `ignore-bots` produce no message.

## Setup

**1. Create a webhook in Discord**

In the channel that should receive the messages: **Edit Channel > Integrations > Webhooks > New Webhook**. Give it a name and avatar (they are shown as the author of the messages) and click **Copy Webhook URL**.

**2. Store the webhook as a secret**

In the GitHub repository you want to watch: **Settings > Secrets and variables > Actions > New repository secret**, with the name `DISCORD_WEBHOOK` and the webhook URL as the value. Anyone with the URL can post in the channel, so never put it in the code.

**3. Add the workflow**

Create `.github/workflows/discord-notify.yml` in that repository:

```yaml
name: Discord notifications

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
    types: [opened, reopened, closed, ready_for_review]

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - uses: Bernardo2409/Actorion@v1
        with:
          webhook: ${{ secrets.DISCORD_WEBHOOK }}
```

This announces pushes to `main` and pull requests that target `main`. See [Choosing what is announced](#choosing-what-is-announced) for other combinations. The next event posts a message; its run is shown in the repository's **Actions** tab.

**Requirements**

- GitHub Actions must be enabled in the repository being watched (see [Cost](#cost)). Public and private repositories can both use Actorion.
- The runner needs Python 3.10 or later, available as `python3` or `python`. GitHub's own runners (`ubuntu-latest`, `windows-latest`, `macos-latest`) already have it. On a self-hosted runner, install Python first or add an [`actions/setup-python`](https://github.com/actions/setup-python) step before Actorion.

## Choosing what is announced

What is announced is chosen in the workflow's `on:` section, so GitHub only runs the action for the events you want.

- `push` with `branches`: pushes to those branches. Patterns work: `'release/**'` matches `release/1.0`.
- `pull_request` with `branches`: pull requests whose **target** branch is in the list.
- `pull_request` with `types`: which pull request events. Announced types are `opened`, `reopened`, `closed` (shown as merged or closed), `ready_for_review`, `converted_to_draft` and `synchronize` (new commits pushed to the pull request). Without `types`, GitHub uses `opened`, `synchronize` and `reopened`, which announces every push to an open pull request.

**Pull requests to main**

```yaml
on:
  pull_request:
    branches: [main]
    types: [opened, reopened, closed, ready_for_review]
```

**Pull requests to dev**

```yaml
on:
  pull_request:
    branches: [dev]
    types: [opened, reopened, closed, ready_for_review]
```

**Pushes to specific branches**

```yaml
on:
  push:
    branches: [main, dev, 'release/**']
```

**Only merged pull requests to main**

```yaml
on:
  pull_request:
    branches: [main]
    types: [closed]

jobs:
  notify:
    if: github.event.pull_request.merged == true
    runs-on: ubuntu-latest
    steps:
      - uses: Bernardo2409/Actorion@v1
        with:
          webhook: ${{ secrets.DISCORD_WEBHOOK }}
```

**Different channels for different events**

Create one webhook per channel and one secret per webhook, then use one job per destination:

```yaml
on:
  push:
    branches: [dev]
  pull_request:
    branches: [main, dev]
    types: [opened, reopened, closed, ready_for_review]

jobs:
  pull-requests-to-main:        # #releases
    if: github.event_name == 'pull_request' && github.base_ref == 'main'
    runs-on: ubuntu-latest
    steps:
      - uses: Bernardo2409/Actorion@v1
        with:
          webhook: ${{ secrets.DISCORD_WEBHOOK_RELEASES }}

  pull-requests-to-dev:         # #reviews
    if: github.event_name == 'pull_request' && github.base_ref == 'dev'
    runs-on: ubuntu-latest
    steps:
      - uses: Bernardo2409/Actorion@v1
        with:
          webhook: ${{ secrets.DISCORD_WEBHOOK_REVIEWS }}

  pushes-to-dev:                # #dev
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    steps:
      - uses: Bernardo2409/Actorion@v1
        with:
          webhook: ${{ secrets.DISCORD_WEBHOOK_DEV }}
```

Jobs whose `if` is false are skipped and don't use any minutes.

**Pull requests from a specific branch**

The `branches` filter looks at the target branch. To filter on the branch the pull request comes from, use `github.head_ref`:

```yaml
jobs:
  notify:
    if: startsWith(github.head_ref, 'feature/')
```

**Pull requests from forks**

GitHub doesn't give secrets to workflows started by pull requests from forks, so the webhook is not available. In that case Actorion skips the message with a notice instead of failing the run.

## Inputs

| Input | Default | Description |
|---|---|---|
| `webhook` | (required) | Discord webhook URL. Pass it from a secret: `${{ secrets.DISCORD_WEBHOOK }}` |
| `role` | empty | ID of a Discord role to mention in each message. Only this role can be pinged |
| `ignore-bots` | `false` | Skip commits and pull requests made by bots such as Dependabot or Renovate |
| `max-commits` | `15` | Commits listed in one push message, from 1 to 50. Older ones are summarised as "...and N earlier commits" |
| `embed` | `true` | Post an embed, or plain text with `false` |
| `username` | empty | Name shown as the author of the message. Empty uses the webhook's own name |
| `dry-run` | `false` | Print the message in the job log instead of posting it |

To find a role ID, enable **Developer Mode** in Discord (User Settings > Advanced), then right-click the role in Server Settings > Roles and select **Copy Role ID**. For the mention to notify members, the role must allow anyone to mention it (*Allow anyone to @mention this role*).

## Message format

**Pushes**, as an embed (default):

```
2 new commits on octo-org/example                      (links to the comparison of the push)
[2222222] Fix login redirect - Octo Cat                (each short hash links to the commit)
[3333333] Update README - Octo Cat
GitHub · branch main · Nov 7, 2026, 12:43 UTC
```

As plain text (`embed: false`):

```
2 new commits on octo-org/example (main)
- Fix login redirect - Octo Cat
- Update README - Octo Cat
```

**Pull requests**, as an embed:

```
Pull request merged: #42 Add login with GitHub        (links to the pull request)
dev -> main by octocat
3 commits, 5 files changed, +120 -30
GitHub · octo-org/example · Nov 7, 2026, 12:43 UTC
```

As plain text, the same lines with a link to the pull request at the end.

| Pull request event | Title | Color |
|---|---|---|
| `opened`, `reopened`, `ready_for_review`, `synchronize` | Pull request opened / reopened / marked as ready for review / updated with new commits | Green |
| `closed` and merged | Pull request merged | Purple |
| `closed` without merging | Pull request closed | Red |
| Draft pull requests, `converted_to_draft` | (as above) | Grey |

- Commits are listed oldest first, with the first line of each commit message and its author.
- Dates are written in English and in UTC.
- Mentions inside commit messages and pull request titles, such as `@everyone`, never notify anyone.

## Examples

Mention a role and skip bot commits:

```yaml
      - uses: Bernardo2409/Actorion@v1
        with:
          webhook: ${{ secrets.DISCORD_WEBHOOK }}
          role: "123456789012345678"
          ignore-bots: true
```

Different channels for different branches, each with its own webhook secret:

```yaml
on:
  push:
    branches: [main, dev]

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - uses: Bernardo2409/Actorion@v1
        with:
          webhook: ${{ github.ref_name == 'main' && secrets.DISCORD_WEBHOOK_RELEASES || secrets.DISCORD_WEBHOOK_DEV }}
```

Check the message without posting it:

```yaml
      - uses: Bernardo2409/Actorion@v1
        with:
          webhook: ${{ secrets.DISCORD_WEBHOOK }}
          dry-run: true
```

A complete workflow with every input is in [`examples/discord-notify.yml`](examples/discord-notify.yml).

## Trying it on your computer

The action is a single Python script that only uses the standard library, so it can run locally with an example push event. This is useful to see a message in Discord without GitHub Actions.

```powershell
$env:ACTORION_WEBHOOK = "https://discord.com/api/webhooks/..."
python actorion.py --event examples/push-event.json
python actorion.py --event examples/pull-request-event.json --event-name pull_request
```

On Linux or macOS:

```bash
ACTORION_WEBHOOK="https://discord.com/api/webhooks/..." python3 actorion.py --event examples/push-event.json
```

Every input can be set the same way, as `ACTORION_` followed by its name in capitals with `_` instead of `-` (for example `ACTORION_DRY_RUN=true` or `ACTORION_MAX_COMMITS=5`).

## Cost

- **Discord webhooks** are free.
- **GitHub Actions** is free and unlimited for public repositories. For private repositories, the free plan includes a monthly allowance of minutes shared by all your private repositories. Each push uses about one minute, because GitHub rounds every job up to the minute.

Pricing and allowances can change; check your repository owner's **Settings > Billing and licensing** for the current values.

## Troubleshooting

| What you see | What to do |
|---|---|
| `Input "webhook" is empty` | The `DISCORD_WEBHOOK` secret is missing in this repository, or its name is different in the workflow |
| `Discord rejected the webhook (HTTP 404)` | The webhook was deleted in Discord. Create a new one and update the secret |
| The run fails before starting (`startup_failure`) | GitHub Actions can't start in this repository, usually because of the account's Actions billing or limits for private repositories |
| `Unable to resolve action Bernardo2409/Actorion` | The name or version in `uses:` is wrong. Use `Bernardo2409/Actorion@v1` |
| `python3: command not found` | The runner has no Python. Add an `actions/setup-python` step before Actorion (see [Requirements](#setup)) |
| No message after a push | The branch is not in the workflow's `branches` list, or all commits were filtered (for example by `ignore-bots`) |
| No message for a pull request | Its target branch is not in the `pull_request` `branches` list, its event is not in `types`, or it comes from a fork |
| A message for every push to a pull request | The workflow's `pull_request` has no `types`, so GitHub includes `synchronize`. Add `types: [opened, reopened, closed, ready_for_review]` |

## Development

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest
```

The tests replace Discord with a fake, so they run offline. They cover the inputs, push and pull request messages in both formats, every pull request event, filters, forks, limits, retries when Discord asks to slow down, error messages, and that every input in `action.yml` reaches the script.

The same tests run on GitHub Actions for every push and pull request, on Linux, Windows and macOS and on the oldest and newest supported Python. That workflow also runs the action itself in dry-run mode, so a broken `action.yml` is caught before a release.

```
Actorion/
  .github/workflows/tests.yml Runs the tests and a dry run of the action on every push and pull request
  action.yml                  Action definition: inputs and the step that runs the script
  actorion.py                 Reads the push event, builds the message and posts it
  examples/
    discord-notify.yml        Workflow to copy into the repositories you want to watch
    push-event.json           Example push event, for trying the script locally
    pull-request-event.json   Example pull request event (merged)
  tests/                      Offline test suite
  requirements-dev.txt        Test dependencies
```

Releases follow semantic versioning. The `v1` tag always points to the latest `v1.x.y` release, so workflows using `@v1` get fixes automatically.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
