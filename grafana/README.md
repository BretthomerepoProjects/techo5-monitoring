# Grafana system metrics: panels and dashboard

Use the standalone **panel JSON** files to add these visualizations to an existing dashboard, or import the complete dashboard for an automatically repeated fleet view.

## Requirements

- Grafana with its built-in Prometheus data source, stat, gauge and time-series panels. No third-party panel plugin (including animated or wavy gauge plugins) is required.
- A Prometheus server scraping each satellite's Node Exporter. The integration manages the exporter; it does not configure Prometheus or Grafana.
- The CPU, meminfo, loadavg, netdev and filesystem collectors enabled by Satellite Manager. The supplied network panel expects `wlan0`; storage expects `/data`.
- Permission to add a Grafana data source and edit/save dashboards, or an administrator who can prepare those resources.

The fleet dashboard layout has been visually checked with nine satellites in the project's Grafana deployment. Compatibility with every Grafana version has not been tested; it uses the classic dashboard JSON model (schemaVersion 39). Standalone panel JSON is derived from that same layout; importing those files into other deployments still needs local validation.

## 1. Prepare Prometheus

Adapt [prometheus-scrape.example.yml](prometheus-scrape.example.yml) and merge its job into your existing `scrape_configs`; do not replace your entire configuration. Replace each target placeholder with your satellite address and configured exporter port. Choose a unique `host` label per satellite. The `job_name` must match `(dot.*|spot.*|show.*|satellite.*)-node`; the generic `satellite-node` example works for any supported model.

Configure Satellite Manager's Prometheus source address to the address actually seen by the satellite. Container routing/NAT may make this different from the container's address. Keep the metrics port restricted to the intended scraper; do not expose it publicly. Validate with your installed `promtool check config <config-file>`, then reload Prometheus using your deployment's normal procedure.

In Prometheus, confirm the target is UP and query:

```promql
up{job="satellite-node"}
node_memory_MemTotal_bytes{job="satellite-node"}
rate(node_cpu_seconds_total{job="satellite-node",mode="idle"}[5m])
```

A rate needs at least two successful scrapes. Allow several minutes for the five-minute charts to populate. Configure one target per satellite, without duplicate scrape jobs for the same endpoint.

## 2. Connect Grafana to Prometheus

Under **Connections > Data sources**, add or select **Prometheus**. Enter the URL reachable from the Grafana server/container and use **Save & test**. `localhost` inside a Grafana container refers to that container, not a separate Prometheus container. Keep any required authentication in the data source configuration; these JSON files contain no credentials.

## 3A. Add individual panel JSON to an existing dashboard

Files in [panels](panels):

| File | Visualization |
| --- | --- |
| [status.json](panels/status.json) | Prometheus scrape UP/DOWN |
| [cpu-busy.json](panels/cpu-busy.json) | Average CPU busy percentage, five-minute rate |
| [memory-used.json](panels/memory-used.json) | RAM used, based on MemAvailable |
| [wifi-throughput.json](panels/wifi-throughput.json) | Receive/transmit bits per second on wlan0 |
| [data-storage-used.json](panels/data-storage-used.json) | Unavailable space on /data as a percentage |
| [load.json](panels/load.json) | Neutral 1/5/15-minute load averages |

1. Download the desired panel JSON. In a text editor, replace **every** `REPLACE_WITH_PROMETHEUS_UID` with your Grafana Prometheus data source UID (not its display name). Find the UID in the data source URL/details.
2. Replace **every** `REPLACE_WITH_HOST` with the exact `host` label from Prometheus, for example `satellite-a`. This value is a PromQL regular-expression matcher: escape regex special characters if your label contains them. The supplied simple example host names need no escaping. For one device per panel, use an exact host rather than `.*`.
3. Open your existing dashboard in edit mode and add a temporary panel. Open its **Inspect > Panel JSON** editor (the wording/location can vary by Grafana version). Preserve the temporary panel's unique numeric `id` when replacing its JSON with the downloaded model, then apply the change. The top-level panel `datasource` and every target's `datasource` must reference your UID.
4. Check the displayed data, reposition/resize the panel as desired, and save the dashboard. Repeat for the other files. If your Grafana version exposes panel JSON as read-only, use the dashboard's editable JSON model to insert the panel object into its `panels` array, with a unique `id` and an appropriate `gridPos`, then save.

These are **panel objects**, not complete dashboards. Grafana's **Dashboards > Import** expects a dashboard JSON and should not be used directly with a standalone panel file. No library-panel publishing or plugin installation is required.

For an existing dashboard that already defines a multi-select `satellite` variable, advanced users may replace `REPLACE_WITH_HOST` with `${satellite:regex}` and use a repeated row. The full dashboard below already supplies that configuration.

## 3B. Import the complete fleet dashboard instead

1. Download [satellites-system-health.json](satellites-system-health.json).
2. Open **Dashboards > New > Import** (or **Dashboards > Import**), upload the JSON or paste its contents, and load it.
3. Select your Prometheus data source when prompted. The `${DS_PROMETHEUS}` input maps the panels and variable to that source.
4. Choose a folder and dashboard name, then import. The supplied UID is `satellites-system-health`; if it already exists, choose a new UID for a separate copy or deliberately update the existing dashboard after backing up your customizations.
5. Select **All** in Satellites. One row appears per matching `host` label. Each row has the six panels above. Default time range is the last hour in UTC, with a one-minute refresh.

After adding scrape targets, reload the dashboard to refresh the satellite list. The variable refreshes on dashboard load; the one-minute timer refreshes panel data. Devices with no configured target are not automatically discovered by this dashboard.

For file/API provisioning, Grafana's interactive import input mapping is not available: replace every `${DS_PROMETHEUS}` with the actual provisioned data source UID yourself before provisioning. Do not put credentials into the dashboard.

## Interpretation and troubleshooting

- **No satellite rows:** check the job naming pattern, `host` labels, selected data source, and dashboard reload.
- **DOWN:** Prometheus cannot scrape that configured endpoint. Check exporter state, port, route and the permitted scraper source address. HA's process sensor is not proof of a successful scrape.
- **No data:** check the selected time range and query the metric in Prometheus. New CPU/network rate panels need two scrapes. Verify datasource UIDs in all panel targets. Missing `wlan0` or `/data` requires adapting those matchers to the device.
- **Repeated or mixed data:** use unique host labels and avoid duplicate scrape jobs for a satellite. For individual panels, select one host or deliberately aggregate the queries.
- **High load with moderate CPU:** load includes tasks waiting in the kernel; it is not a CPU percentage. The load panel intentionally has no overload threshold.
- **Gaps after reboots:** charts show actual scrape history; the dashboard does not start or redeploy exporters. Enable automatic recovery in Satellite Manager separately if desired.

These visualizations are for system metrics only. They do not export or display logs, provide voice-service health checks, or define alerts. HACS updates do not automatically import/update Grafana dashboards. Back up customized JSON before applying a later version. The project remains experimental; its repository-wide license status also applies to these files.

References: [Grafana JSON import](https://grafana.com/docs/grafana-cloud/learn-and-build/visualizations/dashboards/build-dashboards/import-dashboards/), [Prometheus configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/).
