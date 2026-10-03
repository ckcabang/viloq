# On-call engineer

`poll.py` checks Grafana for firing alerts every minute and hands each new
one to a headless coding agent (Claude Code, `claude -p`) with the
instructions in `instructions.md`. The agent investigates and, if it finds a
real bug, commits a fix.

```sh
uv run python on-call-engineer/poll.py          # every minute, until Ctrl-C
uv run python on-call-engineer/poll.py --once   # one poll, waits for its agents
```

It needs the `claude` CLI, signed in, on the `PATH`, and for the agent's test
runs, Postgres: `docker compose up -d db`, which the agent starts itself if
it is not running.

## What happens when an alert fires

1. Grafana's Alertmanager API lists the alert: active, not silenced, and
   matching `ON_CALL_FILTER` (by default `service="viloq"`, the
   `ViloqServerErrors` rule in `observability/alerts/viloq.yaml`). Silence it
   in Grafana and the agent leaves it alone.
2. The poller makes a git worktree of `main` beside the repository, in
   `../viloq-on-call/<time>-<alert>-<fingerprint>/`, on a new branch
   `on-call/<time>-<alert>-<fingerprint>`.
3. It starts the agent there and passes it `instructions.md`, then the alert:
   labels (service, environment, version, owner), annotations (summary,
   runbook, dashboard URL), start time, and the commit its version was built
   from. The alert goes in as data, marked as such.
4. The agent's final reply goes to `reports/<same name>.md`, what it printed
   to stderr to `.log` beside it. Once it exits, the poller logs how many
   commits it left on the branch.

Each firing is handed over once. An alert still firing on the next poll, or
after a restart, is not handed over again; `state.json` records what was. One
that resolves and later fires again is new. If an agent fails, that is
logged, not retried.

## Stopping it

Ctrl-C stops the poller, but not an agent it started: the agent finishes its
investigation and writes its report. The poller names any still running,
with the command that stops one and whatever it started:

```sh
taskkill //PID <pid> //T //F    # Git Bash on Windows; `taskkill /PID <pid> /T /F` in cmd or PowerShell
kill <pid>                      # macOS, Linux
```

The `agent <pid> on on-call/...` line logged when it started has the same
PID. Then remove its worktree and branch as below.

## Reviewing what it did

The fix is a commit on the `on-call/...` branch, nowhere else: not on
`main`, which deploys on push, not in the checkout you work in, and not
pushed. To take it:

```sh
git log -p main..on-call/<name>           # read it
git push -u origin on-call/<name>         # then open a PR as usual
git worktree remove ../viloq-on-call/<name> && git branch -D on-call/<name>   # or drop it
```

## What the agent may do

`claude -p` runs with `--permission-mode dontAsk`: nobody is there to approve
a tool call, so what is not allowed is refused. Allowed: reading and editing
files, `uv run` and `uv sync`, `docker compose -p viloq up -d db` (the
checkout's own database, not a second one per worktree), and `git status`,
`diff`, `log`, `show`, `add` and `commit`. Claude Code also lets through
commands it judges read-only, such as `cat`, `grep`, `docker ps` and
`docker inspect`. Anything else is refused, e.g. `docker exec ... createdb`
in a test run. `git push` is refused, and one investigation stops at $5
(`--max-budget-usd`).

`uv run` is what lets it run the tests and reproduce the failure, and it
runs any code: the agent can do on this machine whatever a Python script
can. The worktree keeps its commits apart; it is not a sandbox. Run the
poller where that is acceptable, e.g. a dev machine or a VM with no
production credentials.

The agent shares the test database with you: the tests empty it on every
run, so agents run one at a time by default, and a test run of yours while
one is working can fail either.

## Settings

All environment variables, all optional:

| Variable               | Default                       | What                                       |
| ---------------------- | ----------------------------- | ------------------------------------------ |
| `GRAFANA_URL`          | `http://localhost:3000`       | The Grafana to ask, e.g. `https://<stack>.grafana.net` |
| `GRAFANA_TOKEN`        | none                          | A service account token (Viewer role) for Grafana Cloud; the local stack needs none |
| `ON_CALL_FILTER`       | `service="viloq"`             | Alertmanager matchers, comma-separated, all of which must match |
| `ON_CALL_AGENT`        | `claude -p ...`, see `poll.py` | The agent command; it gets the prompt on stdin and replies on stdout |
| `ON_CALL_INSTRUCTIONS` | `instructions.md`             | What the agent is told before the alert    |
| `ON_CALL_BASE`         | `main`                        | The branch each worktree starts from       |
| `ON_CALL_WORKTREES`    | `../viloq-on-call`            | Where the worktrees go                     |
| `ON_CALL_REPORTS`      | `reports/`                    | Where the agents' replies go               |
| `ON_CALL_STATE`        | `state.json`                  | Which firings were handed over             |
| `ON_CALL_MAX_AGENTS`   | `1`                           | Agents at once; more share the test database |

For Grafana Cloud, create the token under **Administration → Users and
access → Service accounts**, with the **Viewer** role, which can read
alerts.
