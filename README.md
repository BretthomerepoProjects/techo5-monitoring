# TECHO5 Satellite Manager

![Generic satellite network icon](custom_components/techo5_satellite_manager/brand/icon.png)

A Home Assistant custom integration for inspecting TECHO5 Linux satellites and explicitly managing native Node Exporter metrics through SSH.

**Experimental version 0.3.0.** Device-page controls can deploy and start, start, stop, uninstall, and check Node Exporter. SSH bootstrap remains available through Configure. This release requires device acceptance testing; no stable release has been declared. Automatic recovery is opt-in per satellite. Syslog forwarding is not implemented.

Version 0.2.1 fixes a read-only SSH command race: no stdin EOF is sent when there is no upload. It also distinguishes credential-file, network, negotiation and command-channel failures using sanitized error codes.

Version 0.2.2 fixes connection loss after metrics configuration: the manager script is checksum-verified and streamed through SSH stdin behind a small fixed exec request, avoiding satellite SSH command-size limits. Read-only checks do not write a remote script file.

## Current behavior

- One configuration entry per satellite, with an operator-supplied private IPv4 address, SSH port and local key filename.
- A manual administrator-only check, available directly from the device page.
- Architecture, kernel, root mount flags, online CPU list, load, uptime, aggregate memory and Node Exporter process metadata.
- Strict SSH host-key verification, an 8 KiB output cap, a 20-second metadata timeout, bounded 35-second Node Exporter operations (150 seconds for download/deploy), and serialized operations per entry plus a remote mutation lock.
- After Node Exporter settings are saved, checks run every 60 seconds. With recovery enabled, monitoring can start or repair the managed exporter. Manual Check remains read-only. SSH key provisioning is always explicit. No raw log, audio, command-line, application configuration or credential collection.

ARMv7 and ARM64 are accepted by the initial inspection probe. Architecture detection does not establish firmware or deployment compatibility. A process observed as non-root is not automatically an approved security configuration. Status and attributes describe the latest check. A running process is not evidence that Prometheus has successfully scraped it.

## Requirements

SSH bootstrap has been tested with Home Assistant 2026.9.3 and AsyncSSH 2.21.0. Manual Node Exporter controls have been piloted; automatic recovery has synthetic lifecycle/SSH tests and still requires operator reboot acceptance. It requires a compatible TECHO5 Linux satellite exposing an authorized SSH login and `/etc/techo5/boot.sh`. The integration uses the root SSH login. Explicit Node Exporter actions install a native executable under `/data/ha-satellite-manager/node`, run it as root on the satellite, and change only rules for its configured metrics endpoint. Root use here is limited to satellites; it is not a requirement for other monitoring servers. Decide whether that access is appropriate before installing it.

## Install with HACS (custom repository)

1. Open **HACS > three-dot menu > Custom repositories**.
2. Enter `https://github.com/BretthomerepoProjects/techo5-monitoring`.
3. Select **Integration** as the type and select **Add**.
4. Find **TECHO5 Satellite Manager**, open it, and select **Download**. Select the latest versioned release offered by HACS. The default branch remains available for explicit development testing. This remains an experimental prototype.
5. Restart Home Assistant during your maintenance window, then configure the integration under **Settings > Devices & services**.

For a manually installed copy, back up the existing component code before allowing HACS to replace it. Keep the existing HA configuration entry and the credentials outside the component directory. If HACS reports a conflict, resolve the code-directory conflict; do not delete SSH credentials or the complete HA configuration. Downloading the same domain should preserve the existing configuration entry, but validate it after restart.

This is custom-repository support, not acceptance into the default HACS store. A reuse license has not been selected; the HACS validator explicitly skips only that check for this prototype. Versioned GitHub releases are used for HACS update notifications; the software remains an experimental prototype.

## Installation

1. Back up your Home Assistant configuration and make sure you know how to restore it.
2. Copy only `custom_components/techo5_satellite_manager` into your HA configuration's `custom_components` directory. Do not copy a repository checkout over your whole configuration.
3. Prepare local credentials inside HA's configuration mount:

   ```text
   /config/satellite_manager/ssh/<your-key-filename>
   /config/satellite_manager/ssh/known_hosts
   ```

   These files are **not supplied by this repository**. Use a dedicated key where possible. The current prototype accepts an unencrypted key for unattended use; it cannot prompt for or store a passphrase. Keep the credential directory restricted to the HA runtime account (typically mode 0700), and the private key restricted (0600). Never change HA's runtime identity merely to make files readable. Keep keys outside `/config/www`.

4. Populate `known_hosts` only with host keys independently verified through a trusted connection. Do not disable verification or blindly trust key-scan output.
5. Restart Home Assistant during an appropriate maintenance window.
6. Open **Settings → Devices & services → Add integration → TECHO5 Satellite Manager**. Enter the satellite's private IPv4 address, SSH port and key filename. The key field accepts a filename, not a path.
7. Open the new device page and press **Check satellite**. Its status sensor initially shows `not_checked` and then the result or a short error code. Sensor attributes contain the inspection metadata.

The check requires an HA administrator. Home Assistant Container supports this custom integration; no Supervisor app is needed.

## Prepare SSH during setup

When adding a satellite, **Prepare SSH through ESPHome before testing** is enabled by default. The satellite must already be paired with HA's ESPHome integration using API encryption.

1. Enter its private IPv4 address and the filename of the unencrypted SSH key stored locally in HA.
2. Select the satellite's SSH switch. Setup matches the switch to the connected ESPHome device's IP and node-specific `ssh_keys` action; a different device or unencrypted API connection is refused.
3. Review the target and local key fingerprint. Include additional public keys to retain and explicitly confirm replacement of the authorized-key list.
4. Setup enables the SSH switch, derives and sends only public keys through ESPHome, and waits for the SSH listener.
5. For a first connection, explicitly confirm the displayed SSH host-key fingerprint. Compare it through an independently trusted channel when available. A saved key mismatch is refused, never silently replaced.
6. Setup tests SSH with the configured key and strict saved-host verification before creating the entry. A failed test can be retried without pushing keys again.

For an existing Satellite Manager entry, **Configure > Prepare SSH and test connection** opens this same explicit provisioning flow. No key provisioning runs on startup, on a check-button press, or on a timer. Config entries store filenames and connection settings, never private keys, public-key lists or passphrases. Device provisioning uses the existing encrypted ESPHome link and does not require SSH to be working first.

Provisioning changes device security settings. A failed connection test does not restore the previous authorized-key list or turn SSH off. Include every public key you want retained; the API cannot retrieve the old list for automatic merging. A timed-out action may still complete on the device, so check its state before retrying provisioning. This is not rollback of firmware and never starts a firmware update.

## Node Exporter metrics

1. Open the integration entry's **Configure > Configure Node Exporter**. Enter the private IPv4 source address from which Prometheus connects and the exporter TCP port (default 9100). This is the Prometheus server address, not the HA address unless they are the same host. Saving settings does not deploy anything.
2. On the satellite device page, press **Check satellite**. Review its architecture, kernel and existing exporter state. Initial profiles require the TECHO5 boot marker and ARMv7 with a 3.18 kernel, or ARM64 with a 4.9 kernel. Other profiles refuse deployment pending validation.
3. Press **Deploy and start Node Exporter**. HA downloads the architecture-specific upstream v1.12.1 archive over verified HTTPS, checks its pinned archive and executable SHA256 values, and streams only the executable through strict SSH. No satellite SCP/SFTP, Internet connection, or container runtime is required. The executable is checked again remotely and installed with mode 0700 under root-owned 0700 directories. Uploads are capped at 32 MiB; at least 64 MiB of free `/data` space is required.
4. The manager adds an allow rule for the configured Prometheus source, then a deny rule for other sources, at the front of `INPUT`, before firmware chain jumps or existing accepts. Both rules match only the satellite address and configured TCP port. It uses the legacy iptables backend. New rule ownership is recorded in a protected `firewall_mode` manifest, so no kernel comment-match extension is required; earlier comment-tagged installations remain recognized; no unrelated rules, saved firmware rules, boot slots, root mounts, or init scripts are changed. The last pre-change chain snapshot, with a UTC header, remains in `firewall.before` until uninstall. Failed starts remove only new rules and the new managed process; verified installed files remain available for a retry.
5. Configure a matching scrape target in your Prometheus server separately. The integration does not edit Prometheus. Confirm the target is **UP** and fresh `node_cpu_seconds_total`, memory, load, network, filesystem and uname samples arrive. The HA **Node Exporter** sensor reports process state and firewall observations, not scrape success.

The exporter enables only CPU, memory, load, network, filesystem and uname collectors, disables exporter self-metrics, and discards its own stdout/stderr so it cannot grow a local log file. Credentials and integration options stay in HA configuration; no native payload is packaged in public Git or the integration zip.

**Start** uses a verified existing installation without downloading. Start and Deploy enable persistent automatic recovery. **Stop** first persistently disables recovery, then terminates only a process matching the managed executable checksum, saved PID and process start time and removes only the manager's exact endpoint rules. **Uninstall** also removes only the known managed files. Stop and Uninstall disable recovery before contacting the satellite, even if it is unreachable. If another operation is active, Stop waits for it to finish before stopping the process; it does not race a deployment. To change endpoint settings, Stop first, save settings, then Deploy and start again. Removing the HA entry does not stop a remote process: use Stop or Uninstall first.

Existing unmanaged exporters or init/firmware respawn installations are refused. Do not repeatedly kill them; they need a separate targeted adoption plan. A remote-operation lock is never stolen automatically. After a timeout, disconnect, or failed upload, inspect the device before retrying. An interrupted upload can leave a bounded temporary file or a lock needing operator review; errors never grant broader permissions or disable host verification.

This integration does not create boot hooks or save firewall rules into firmware. Processes and rules may disappear on reboot or firmware updates; opt-in recovery restores managed services through HA. Firmware updates remain outside these controls.

## Automatic Node Exporter recovery

On each device page, turn on **Automatic Node Exporter recovery** to request an immediate check and recovery. Existing entries default to OFF on upgrade, preserving earlier manual Stop intent. Start or Deploy also turns it on. The choice is stored in HA entry options and survives an HA restart; the first scheduled check runs within approximately 60 seconds after the entry loads. No remote operation runs during integration setup itself.

- A healthy managed exporter is left running.
- A valid retained installation is started; the pinned payload is not downloaded again.
- A missing payload is downloaded, verified, installed and started for the detected supported profile.
- A damaged or non-executable managed payload can be repaired only with safe directory/file ownership and a matching ownership manifest. Its previous binary is retained as one bounded `node_exporter.previous` backup. Unknown installations, unsafe permissions, mismatched metadata, external supervisors and unrecognized processes require review.
- Missing managed firewall rules are restored by Start. Partially changed rules require review.

Failures back off automatic mutation attempts by 1, 2, 4, 8 and then 15 minutes maximum. Read-only monitoring continues so an unreachable device returning online can be noticed. Current intent, failure count, sanitized error and next retry time are visible as sensor attributes. Only one operation runs per satellite, and the remote mutation lock is retained. Healthy monitoring does not restart processes or redownload payloads.

Turning the recovery switch OFF disables future automatic actions without stopping a running exporter. Use Stop or Uninstall to disable recovery and stop the service. A pending Stop blocks another enable request. Manual Check never starts, installs, changes firewall rules or provisions keys, even while recovery is enabled.

HA must be online and existing strict SSH access must work. Changed host keys, firmware incompatibility or reset SSH settings are refused; recovery never replaces keys, remounts firmware or broadens permissions. Test a single satellite reboot first, verify Start without redownload, then verify fresh Prometheus metrics. Reinstallation after missing/damaged payloads requires separate acceptance. Before downgrading, disable recovery and use this version's Stop/Uninstall controls, particularly if a repair backup exists.

## Removal and rollback

Disable or remove the integration entry. If the integration prevents startup, stop HA and move only the `techo5_satellite_manager` component folder out of `custom_components`, then start HA again. Preserve your original configuration and SSH access. Removing the integration does not disable satellite SSH or restore a key list changed during optional provisioning. Use the existing trusted ESPHome controls to restore those device settings if needed.

HA backups which include the credential directory contain sensitive SSH access. Protect those backups and verify that credentials are restored with the intended permissions. Nothing from your live configuration should be committed to this repository.

## Development

Use a disposable virtual environment:

```sh
python -m venv .venv
# Activate the environment using the command for your operating system.
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
python tools/check_public_tree.py
python tools/build_package.py
```

Tests use synthetic metadata and an ephemeral localhost SSH server with keys generated in temporary directories. No real HA account, satellite, network inventory or secret is needed. The package builder includes only allowlisted component source and writes an archive under ignored `dist/`.

The unit and SSH transport tests do not prove full HA lifecycle behavior or compatibility with every TECHO5 firmware. Test new versions on your own setup with a rollback ready. Source changes never automatically install into HA.

## Public repository policy

All source and documentation here must be safe for public distribution. Credentials, host-key files, real device inventories, raw logs, household-specific dashboards, production configurations, binaries and backups are excluded. Use synthetic examples in issues and remove identifying details from screenshots.

Licensing has not yet been selected. Public visibility is not a claim of a stable or licensed open-source release. No affiliation with upstream TECHO5, Home Assistant or Prometheus is implied.

## Updating through Home Assistant

Versioned GitHub releases are the update source for HACS. After a new release is published, HACS refreshes its metadata and updates its Home Assistant update entity. To refresh immediately, open this repository in HACS and use **Update information**, then open **Settings > System > Updates**. Installing integration code requires a Home Assistant restart before the new Python code runs. Publication does not automatically install or restart anything.

### Recovering after a partially completed SSH setup

If SSH was enabled and the key was sent but setup then failed, start Add Integration again and turn off **Prepare SSH through ESPHome before testing**. Setup now performs only a public host-key handshake, explicit first-use trust if needed, and the SSH connection test. It does not resend or replace keys or change the switch. Version 0.1.4 fixes blocking file access during AsyncSSH options construction by performing it in HA's executor and disabling unused host-based authentication keys.
