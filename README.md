# Actorion

Actorion is a GitHub Action that posts the commits of every push to a Discord channel through a webhook.

There is no bot to run and no server to keep online: GitHub runs the action on its own machines whenever you push, and the message arrives in Discord a few seconds later.

## Contents

- [How it works](#how-it-works)
- [Setup](#setup)
- [Inputs](#inputs)
- [Message format](#message-format)
- [Examples](#examples)
- [Trying it on your computer](#trying-it-on-your-computer)
- [Cost](#cost)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [License](#license)

## How it works

1. You push to a repository that has the Actorion workflow.
2. GitHub starts a temporary machine and runs the action, which reads the commits of that push.
3. The action sends one message to your Discord webhook URL.
4. Discord posts it in the channel the webhook belongs to, under the webhook's name and avatar.

Only the push itself is announced: each push produces one message listing its commits. Deleted branches, tag pushes without commits and pushes made only of filtered commits produce no message.

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

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - uses: Bernardo2409/Actorion@v1
        with:
          webhook: ${{ secrets.DISCORD_WEBHOOK }}
```

Remove the `branches` line to announce pushes to every branch. The next push posts a message; its run is shown in the repository's **Actions** tab.

**Using Actorion from private repositories**

The Actorion repository is private. Other repositories of the same owner can use it because its **Settings > Actions > General > Access** is set to *Accessible from repositories owned by the user*. GitHub Actions must also be able to run in the repository being watched (see [Cost](#cost)).

## Inputs

| Input | Default | Description |
|---|---|---|
| `webhook` | (required) | Discord webhook URL. Pass it from a secret: `${{ secrets.DISCORD_WEBHOOK }}` |
| `role` | empty | ID of a Discord role to mention in each message. Only this role can be pinged |
| `ignore-bots` | `false` | Skip commits made by bots such as Dependabot or Renovate |
| `max-commits` | `15` | Commits listed in one message, from 1 to 50. Older ones are summarised as "...and N earlier commits" |
| `embed` | `true` | Post an embed, or plain text with `false` |
| `username` | empty | Name shown as the author of the message. Empty uses the webhook's own name |
| `dry-run` | `false` | Print the message in the job log instead of posting it |

To find a role ID, enable **Developer Mode** in Discord (User Settings > Advanced), then right-click the role in Server Settings > Roles and select **Copy Role ID**. For the mention to notify members, the role must allow anyone to mention it (*Allow anyone to @mention this role*).

## Message format

As an embed (default):

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

- Commits are listed oldest first, with the first line of each commit message and its author.
- Dates are written in English and in UTC.
- Mentions inside commit messages, such as `@everyone`, never notify anyone.

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
| `Unable to resolve action Bernardo2409/Actorion` | The repository using the action can't access it. Check Actorion's **Settings > Actions > General > Access** |
| No message after a push | The branch is not in the workflow's `branches` list, or all commits were filtered (for example by `ignore-bots`) |

## Development

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest
```

The tests replace Discord with a fake, so they run offline. They cover the inputs, the message in both formats, filters, limits, retries when Discord asks to slow down, error messages, and that every input in `action.yml` reaches the script.

```
Actorion/
  action.yml                  Action definition: inputs and the step that runs the script
  actorion.py                 Reads the push event, builds the message and posts it
  examples/
    discord-notify.yml        Workflow to copy into the repositories you want to watch
    push-event.json           Example push event, for trying the script locally
  tests/                      Offline test suite
  requirements-dev.txt        Test dependencies
```

Releases follow semantic versioning. The `v1` tag always points to the latest `v1.x.y` release, so workflows using `@v1` get fixes automatically.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
