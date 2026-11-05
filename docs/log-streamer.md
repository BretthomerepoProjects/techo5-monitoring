# Managed log streamer (experimental)

The log streamer is an optional service in TECHO5 Satellite Manager. It reads the Linux kernel message buffer and selected existing log files, then sends newline-framed RFC3164-style syslog records over TCP. It does not use the TECHO5 remote API, require an image rebuild, install a syslog daemon, change source log levels, edit startup scripts, or open an inbound network port. Grafana is not part of log collection or viewing.

## Set up

1. Update Satellite Manager and restart Home Assistant in your maintenance window. Existing Node Exporter settings remain separate; logging and its recovery default to unconfigured/off.
2. Prepare a TCP syslog collector on a trusted private network. The default destination port is1514. Match the collector's framing to newline-delimited syslog (not octet-counting only), and allow traffic from each satellite. The integration does not configure your receiver or indexer. No credentials are required by this sender; it does not support TLS or authenticated destinations in this version.
3. Open the satellite integration entry's **Configure > Configure log streamer**. Enter the collector's private IPv4 address and TCP port. Review and confirm forwarding existing log contents. Saving settings does not deploy or start the process.
4. Press **Check satellite**, then **Deploy and start log streamer** on its device page. The integration downloads the architecture-specific pinned executable using verified HTTPS and checks SHA256 locally and remotely. The satellite requires no Internet access, compiler, package manager, SCP or SFTP.
5. Check the **Log streamer** sensor and its attributes. Independently confirm fresh indexed records from that satellite at your collector. Running, connected and written counters are observations, not delivery acknowledgements or proof of indexing.

**Start** uses the verified retained executable. Start and Deploy enable **Automatic log streamer recovery**. Stop and Uninstall persistently disable that recovery before contacting the device, even when it is offline. Disabling the recovery switch alone does not stop a running process. Stop and Uninstall wait for an existing operation rather than racing a deployment. Node Exporter recovery and logging recovery are independent. Manual Check remains read-only.

To change the destination: Stop the log streamer, change its settings, then Deploy and start. Removing the HA entry does not terminate a remote process: Stop or Uninstall it first. Recovery depends on Home Assistant being available and able to connect over SSH; no firmware boot hook is installed. Recovery checks run every60seconds and failed mutations back off up to15minutes. Unknown processes, unsafe ownership or mismatched manifests require review rather than adoption.

## Sources on each model

Each path below is a fixed allowlist entry. Missing optional files are retried; no recursive directory scan or wildcard collection runs. Files must be regular, root-owned files, not symlinks. The architecture/kernel profile and TECHO5 boot marker are checked before operations. Kernel access and the model family's main application file are required to start.

| Source | Dot | Show5 | Show8 | Spot |
|---|---|---|---|---|
| Kernel | `/dev/kmsg` | `/dev/kmsg` | `/dev/kmsg` | `/dev/kmsg` |
| Main application | `/data/techo5-linux/echod.log` | `/data/techo5-linux/techo5.log` | Same as Show5 | Same as Show5 |
| Wi-Fi / DHCP | `/data/techo5-linux/wpa.log`, `udhcpc.log` | `/tmp/wpa.log`, `/tmp/udhcpc.log` | Same as Show5 | Same as Show5 |
| Bluetooth chipset | `/data/techo5-linux/wmtup.log` | Not separately collected | Not separately collected | Not separately collected |
| Bluetooth / audio / D-Bus | `bluetoothd.log`, `btbridge.log`, `bluealsa.log`, `dbus.log` under `/data/techo5-linux` | Same filenames under `/var/log` | Same as Show5 | Same as Show5 |
| Boot / recovery | `/data/techo5-linux/first-boot.log` if present | `/run/boot.log`; `boot.log`, `rescue.log`, `firewall.log` under `/data/techo5-linux` | Same as Show5 | Same as Show5 |
| NTP | `/tmp/ntpd.log` | `/tmp/ntpd.log` | `/tmp/ntpd.log` | `/tmp/ntpd.log` |

Some files are startup evidence rather than continuously updated service logs. Empty files generate no records. This version does not reconfigure services whose output is discarded, and does not claim complete SSH authentication, Wi-Fi or DHCP event coverage. Node Exporter's own discarded output is not an existing file source. No audio, application settings, SSH keys or arbitrary files are collected.

From v0.4.1, records use the integration's configured device name as the syslog hostname. Spaces and characters outside ASCII letters, digits, dots, underscores and hyphens become hyphens; tokens are limited to63characters with a hash suffix when shortened. An entirely unsupported name gets a deterministic token. Choose distinct names for devices. Entries without a name retain their address as a fallback. The network source address remains available to the collector independently. This is a display name, not a hardware identifier, and renaming it does not rewrite previously indexed logs.

The v0.4.0 streamer's reviewed payload is safely upgradeable: the manager verifies the replacement before stopping the owned old process, preserves the old executable as `previous`, then starts the new build. Automatic streamer recovery performs this upgrade when enabled; otherwise use Deploy and start. Stop still controls the verified old process. Unknown executable hashes or processes are refused. A failed replacement download leaves the old process running. Updating a name after deployment requires Stop, then Deploy and start; process startup replays retained history as described below.

Records use source tags such as `satellite-kernel`, `satellite-application`, `satellite-dhcp` and `satellite-bluetooth`. Kernel messages retain sequence number and monotonic microsecond time; all records include the boot identifier. The outer timestamp is UTC forwarding time, not a reconstructed original wall-clock time. Existing application timestamps remain in the message body. Preserve collector UTC receipt time too. File messages use a neutral informational syslog priority; their original severity text remains unparsed in the body. Kernel syslog priority is preserved.

## Lightweight operation and delivery limits

- One static native process, no interpreter/container or per-file `tail` process. Reviewed payloads are under100KiB. In short four-model pilots, resident RAM was approximately260KiB on Dot and320KiB on Show5/Show8/Spot, with one thread. These are observations, not universal upper bounds.
- Fixed96-slot in-memory frame queue, about240KiB; no continuous flash spool. At most32KiB is read per file scan, file scans occur every2seconds, and the main loop is limited to four passes per second even during floods. Parsing and indexing belong on the collector.
- A full queue pauses source reading. Files can rotate and kernel buffers can wrap while a collector is unavailable, so loss is still possible. Kernel sequence gaps and observed file rotation/truncation gaps are exposed. Copy/truncate races can lose messages without an exact loss count; this is best-effort collection, not an audit archive.
- On each process start, files replay up to their last8KiB (discarding an initial partial line); kernel collection starts at the oldest retained kernel record. Restart/Start/recovery can therefore replay earlier records. Use boot identifier plus kernel sequence to deduplicate kernel events if desired. No persistent cursor is written to flash.
- File message bodies are capped at2047bytes; oversized lines are drained incrementally, explicitly marked `truncated=1` and counted. Embedded control characters are flattened so a source cannot forge extra newline-framed syslog records. Kernel record bodies are also bounded.
- One nonblocking outbound TCP connection; connect timeout5seconds, blocked-write reconnect after10seconds, exponential retries up to60seconds with a small jitter. A partial record is discarded on disconnect so it is not resumed mid-record on a new stream. TCP writes do not acknowledge collector persistence.
- Short pilot samples after a40second settling period observed about0.02–0.11% of one CPU core. Large initial kernel-history replay used more CPU (about0.62% on the sampled Dot). Voice/music workloads, prolonged outage, firmware updates and reboot acceptance still need operator testing; no guarantee of those measurements under every workload.

The scalar runtime status file lives under a protected directory in `/run`, not in a growing persistent log. Counters reset on process restart: connected, frames written, dropped partial/overflow frames, truncations, observed kernel sequence losses, observed file gaps, read errors, source-file count, queue depth, kernel-open state, process CPU microseconds and monotonic status time. Home Assistant stores only these observations, not log contents. A missing counter is unknown, not a confirmed zero. `log_ingestion_verified` remains false because this integration has no collector acknowledgement protocol.

Raw file logs can contain personal text, room names, URLs or identifiers. Unlike TECHO5's redacted live-log API, this collector forwards existing file contents with framing sanitization only. Use a private trusted collector and suitable access/retention settings. Plain TCP does not encrypt messages. A power failure loses RAM-buffered records, and no sender can guarantee delivery of the final messages before a crash or network failure.

## Validation and scope

Initial temporary pilots exercised deploy, read, stop, start and uninstall on Dot, Show5, Show8 and Spot. Kernel and existing file records reached a TCP collector and were verified in its index through aggregate counts. Revised startup tests reported no queue drops on the four test devices. Synthetic tests cover file rotation/truncation, long and partial lines, framing injection, bounded queues, kernel sequence gaps, connection changes, unsafe paths and integration lifecycle/recovery. No fleet-wide logging deployment, firmware rebuild, service output reconfiguration or reboot was performed by those pilots.

The streamer is delivered to `/data/ha-satellite-manager/streamer` through the same strict SSH transport as Node Exporter. It runs as root only on satellites to access kernel records. PID/start-time/executable hash and protected manifests constrain Stop/Uninstall. There are no logging firewall changes. Runtime dependencies/third-party notices accompany the component; [native source and build instructions](../native/README.md) are included in the project. SSH key generation is a separate planned feature.
