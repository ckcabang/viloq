# Observability

Where the app's traces and metrics go, and how to look at them. The app sends
them over OpenTelemetry (see `backend/telemetry.py`) to whatever
`OTEL_EXPORTER_OTLP_ENDPOINT` names, and sends nothing while it is unset.

| Where the app runs           | Sends to                          | Look at it in             |
| ---------------------------- | --------------------------------- | ------------------------- |
| Your machine                 | this directory's stack            | http://localhost:3000     |
| Render, `viloq` and `viloq-dev` | Grafana Cloud, on its free tier | your `*.grafana.net` stack |

Both are free. The app is the same in both; only the endpoint, and in the
cloud the API key, differ.

## On your machine

`compose.yaml` here runs Grafana Labs' `grafana/otel-lgtm` image: one container
with an OpenTelemetry Collector in front of Prometheus (metrics), Loki (logs)
and Tempo (traces), and Grafana with all three already added as data sources.
It is its own Compose project, apart from the app's `compose.yaml`; the only
link between them is the endpoint the app is given.

```sh
docker compose -f observability/compose.yaml up -d --wait    # ~10 seconds

OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 VILOQ_ENVIRONMENT=local \
  uv run uvicorn backend.main:app --reload
```

Use the app for a bit, then open http://localhost:3000 (no sign-in). The
**viloq** dashboard (in the `viloq` folder, see [The dashboard](#the-dashboard))
shows what people do with the app; Explore has the rest:

- **Traces** (Tempo): search `{resource.service.name="viloq"}`. Each request
  is a trace, with its queries inside it; a 500 is marked as an error and
  carries the exception and stack trace. Traces show up within seconds.
- **Metrics** (Prometheus): e.g. `http_server_duration_milliseconds_count` or
  `db_client_connections_usage`. They are sent once a minute, so the first
  ones take up to a minute to appear.
- **Logs** (Loki): empty for now; the app does not send logs yet.

## What the app counts

Beside the HTTP and connection-pool metrics, the app counts what people do
with it (`backend/metrics.py`). Each is labelled with the environment and
image tag like every other metric (see below), and otherwise only with a few
fixed values, never an id, an email or an amount.

| Metric (as Prometheus names it)        | Labels                                  | Answers                                          |
| -------------------------------------- | --------------------------------------- | ------------------------------------------------ |
| `viloq_magic_links_requested_total`    |                                         | How many sign-in links are asked for             |
| `viloq_sign_ins_total`                 | `viloq_sign_in_result`                  | New and returning sign-ins, and why links fail: `unknown_link`, `used_link`, `expired_link` |
| `viloq_groups_created_total`           |                                         | Is anyone starting groups                        |
| `viloq_members_joined_total`           |                                         | Do invites get used                              |
| `viloq_expense_changes_total`          | `viloq_operation`, `viloq_split_type`   | Expense activity, and which splits people use    |
| `viloq_payment_changes_total`          | `viloq_operation`                       | Are debts being paid back                        |
| `viloq_settlements_suggested_total`    | `viloq_settlement_strategy`             | Which settlement strategy people look at         |
| `viloq_group_expenses` (histogram)     |                                         | How many expenses a dashboard loads, unpaginated |
| `viloq_api_errors_total`               | `error_type`, `http_route`              | Errors by contract code, which the status alone runs together (409: `version_conflict` or `currency_locked`) |

Counts are taken once a change is saved, so a refused one is not counted.

Every counter series starts at zero when the process starts, errors for every
code on every route included. `rate` and `increase` count nothing for a
series' first sample, and each deploy starts new series (they carry the new
version), so a series that first appeared at 1 would lose the first sign-up,
group or error after every deploy. The histogram cannot start at zero without
a made-up load, so the expenses-per-load panel fills in from the second load
after a deploy.

Some queries to start from:

```promql
# Sign-in links that let nobody in, over the last day, by why
sum by (viloq_sign_in_result) (increase(viloq_sign_ins_total{viloq_sign_in_result=~".*_link"}[1d]))

# Edits lost to someone else's, per minute, in production
sum(rate(viloq_api_errors_total{error_type="version_conflict", deployment_environment_name="production"}[5m])) * 60

# The 95th percentile of expenses per dashboard load
histogram_quantile(0.95, sum by (le) (rate(viloq_group_expenses_bucket[1h])))

# Which split types each deployed version saw
sum by (service_version, viloq_split_type) (increase(viloq_expense_changes_total{viloq_operation="create"}[1d]))
```

A rise in `used_link` is the usual mark of a mail scanner opening links
before the person does, once links are emailed.

## The dashboard

`dashboards/viloq.json` puts these metrics on one dashboard: totals for the
time range picked (sign-ups, failed sign-in links, groups, expenses, edit
conflicts), which version each environment ran and when, then sign-in,
activity and errors over time.

Its **Environment** and **Version** dropdowns filter every panel. Both allow
several values, and both list only what has sent metrics: the versions for
the environments picked, newest image tag first. A run with no
`VILOQ_ENVIRONMENT` or image tag (a local one) only shows under **All**.

- **Locally** it is loaded when the stack starts (`grafana-dashboards.yaml`,
  mounted by `compose.yaml`). Grafana will not save edits to it: change the
  JSON, then `docker compose -f observability/compose.yaml restart` to load
  it. To work on it in the editor instead, use **Save as** for a copy, then
  **Export → Export as JSON** and paste the result over the file.
- **In Grafana Cloud**, import it once: **Dashboards → New → Import**, upload
  `dashboards/viloq.json`, pick a folder and import. It needs no data source
  mapping: its **Data source** dropdown starts at the stack's default
  Prometheus, `grafanacloud-<stack>-prom`. Import it again, overwriting, after
  the JSON changes.

Data is kept in the `lgtm-data` volume across restarts. `down` stops the
stack; `down -v` also deletes what it collected:

```sh
docker compose -f observability/compose.yaml down
```

Its ports are published on `127.0.0.1` only, since this Grafana signs everyone
in as an admin. The image is meant for development, not for hosting.

## The alert

`alerts/viloq.yaml` holds one alert rule, **ViloqServerErrors**: production
answering more than 5% of its requests with a 5xx over 5 minutes, at least 3
of them, and still doing so 5 minutes later. A 5xx is the app failing, not the
person: the expense is not saved, the group does not load, and trying again
does not help. A lone bad request, or the minute or two a deploy or restart
takes, stays under it; the app down on every route reaches it in about 10
minutes. With no requests at all it stays quiet.

It fires once per deployed version, and says:

| Labels        | e.g.                                    |
| ------------- | --------------------------------------- |
| `service`     | `viloq`                                 |
| `environment` | `production`                            |
| `version`     | `20261003-120000-83a1bcf`, the image to roll back |
| `owner`       | `ckcabang`                              |
| `severity`    | `critical`                              |

and in its annotations, the failure rate (`summary`), what to do (`runbook`),
and a `dashboard_url` that opens the dashboard on that environment and
version. Grafana also links the rule to the dashboard's **Requests failing
with a server error** panel, which charts what the rule measures, with its 5%
line, and marks there when the alert fired and resolved.

The file is in Prometheus's rule format, which Grafana imports as
Grafana-managed rules. Import it, and again after it changes:

- **In Grafana Cloud**: **Alerting → Alert rules → More → Import to
  Grafana-managed rules**. Pick **YAML file**, upload `alerts/viloq.yaml`,
  choose the stack's Prometheus (`grafanacloud-<stack>-prom`) as the data
  source and a folder, e.g. `viloq`, and import.
- **Locally**, the same, with `Prometheus` as the data source; or

  ```sh
  curl -X POST http://localhost:3000/api/convert/prometheus/config/v1/rules/viloq \
    -H 'Content-Type: application/yaml' \
    -H 'X-Grafana-Alerting-Datasource-UID: prometheus' \
    --data-binary "$(sed -n '/^  - name:/,$p' observability/alerts/viloq.yaml | sed '1s/^  - //; 2,$s/^    //')"
  ```

  It watches `production` only, so to see it fire here, run the app with
  `VILOQ_ENVIRONMENT=production` and make requests fail, e.g. by stopping
  its database.

An alert nobody receives is not actionable. In Grafana Cloud, check
**Alerting → Contact points**: the default email contact point must name an
address someone reads, or point the default notification policy (or a policy
matching `severity=critical`) at Slack, Grafana IRM or a phone. The local
stack sends notifications nowhere.

To check the whole path without breaking anything, import
`alerts/drill.yaml` the same way, into a folder of its own. Its rule,
**ViloqOnCallDrill**, fires whenever production sends metrics, routed like
ViloqServerErrors and marked as a drill. Its email should arrive within a few
minutes, and the on-call poller (`on-call-engineer/`) should start an agent
that reports a drill and commits nothing. During a deploy it fires once for
each version production ran in the last 5 minutes. Delete the rule, or its
folder, when done.

Rolling back from the Render dashboard (**viloq → Events → Rollback**) brings
the service back on the previous image, but leaves the `production` tag on
the failing one, and a Blueprint sync redeploys what that tag names. Fix
forward, or promote a good image, before the next sync.

## On Render: Grafana Cloud

The deployed services cannot reach your machine, so they send to Grafana
Cloud instead: hosted Grafana, Prometheus, Loki and Tempo, the same set as
above. Its free tier covers 10k active metric series, 50 GB of traces and 50 GB
of logs a month, kept for 14 days, with no card and no expiry. Development and
production share it.

1. Sign up at https://grafana.com and create a stack.
2. In the Grafana Cloud portal, on your stack, open the **OpenTelemetry** tile
   and generate a token. It shows the two values the app needs:
   `OTEL_EXPORTER_OTLP_ENDPOINT` (`https://otlp-gateway-prod-<region>.grafana.net/otlp`)
   and `OTEL_EXPORTER_OTLP_HEADERS`.
3. In the Render dashboard, set both on `viloq` and on `viloq-dev`, under each
   service's Environment, then **Save and deploy**: the app reads them only at
   startup. `render.yaml` declares them with `sync: false`, so they are kept
   out of the repository and a Blueprint sync leaves them alone.

The headers value is the whole of

```
Authorization=Basic <base64 of INSTANCE_ID:TOKEN>
```

with no quotes around it. The tile shows it inside a shell
`export ...="..."`, and Render would keep those quotes as part of the
value. `Authorization=` is required too, and what follows `Basic ` is the
encoded `INSTANCE_ID:TOKEN`, not the bare `glc_...` token. The instance ID is
the one on the OpenTelemetry tile, not the user ID the Prometheus, Loki or
Tempo data sources show. The space after `Basic` may be a space or `%20`.

If it is wrong, the service's Logs say so once a minute, at the latest when
metrics are next sent:

| Logged                                     | Means                                            |
| ------------------------------------------ | ------------------------------------------------ |
| `Failed to export ... code: 401`           | The headers value is wrong; see above.           |
| `Failed to export ... code: 404`           | The endpoint is wrong. It ends in `/otlp`; the app adds `/v1/traces` and `/v1/metrics` itself. |
| nothing, and nothing in Grafana either     | The app is not sending: check the variable name, and that the service was deployed after it was set. |

Each service also has `VILOQ_ENVIRONMENT` (`production` or `development`) from
`render.yaml`, and every trace and metric carries it, so one Grafana Cloud
stack keeps the two apart:

- **Traces**: `{resource.deployment.environment.name="production"}`
- **Metrics**: Grafana Cloud turns it into the `deployment_environment_name`
  label, e.g.
  `http_server_duration_milliseconds_count{deployment_environment_name="development"}`.
  The local stack does the same, so a query you try here works there.

Each is also labelled with `service_version`, the image tag that sent it.
