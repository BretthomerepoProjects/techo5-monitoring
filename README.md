# TECHO5 Satellite Manager

A Home Assistant custom integration for inspecting TECHO5 Linux satellites through SSH.

**Early prototype, version 0.1.2.** The current version offers a device-page **Check satellite** button and a status sensor. It makes no remote changes. Exporter deployment, log forwarding, start/stop controls and automatic recovery are being developed separately and are not available in this version. No stable release has been declared.

## Current behavior

- One configuration entry per satellite, with an operator-supplied private IPv4 address, SSH port and local key filename.
- A manual administrator-only check, available directly from the device page.
- Architecture, kernel, root mount flags, online CPU list, load, uptime, aggregate memory and Node Exporter process metadata.
- Strict SSH host-key verification, a 20-second overall timeout, an 8 KiB output cap, and one check at a time.
- No SSH connection during integration startup. No raw log, audio, command-line, application configuration or credential collection.

ARMv7 and ARM64 are accepted by the initial inspection probe. Architecture detection does not establish firmware or deployment compatibility. A process observed as non-root is not automatically an approved security configuration. The status and attributes are observations from the most recent manual check, not continuous monitoring.

## Requirements

The prototype has been tested with Home Assistant 2026.9.3 and AsyncSSH 2.21.0. It requires a compatible TECHO5 Linux satellite exposing an authorized SSH login and `/etc/techo5/boot.sh`. The current prototype uses the root SSH login; its remote command is a fixed read-only inspection script. Decide whether that access is appropriate before installing it.

## Install with HACS (custom repository)

1. Open **HACS > three-dot menu > Custom repositories**.
2. Enter `https://github.com/BretthomerepoProjects/techo5-monitoring`.
3. Select **Integration** as the type and select **Add**.
4. Find **TECHO5 Satellite Manager**, open it, and select **Download**. With no published releases, HACS uses the default branch. This remains an experimental prototype.
5. Restart Home Assistant during your maintenance window, then configure the integration under **Settings > Devices & services**.

For a manually installed copy, back up the existing component code before allowing HACS to replace it. Keep the existing HA configuration entry and the credentials outside the component directory. If HACS reports a conflict, resolve the code-directory conflict; do not delete SSH credentials or the complete HA configuration. Downloading the same domain should preserve the existing configuration entry, but validate it after restart.

This is custom-repository support, not acceptance into the default HACS store. A reuse license has not been selected; the HACS validator explicitly skips only that check for this prototype. No stable GitHub release is declared.

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

## Removal and rollback

Disable or remove the integration entry. If the integration prevents startup, stop HA and move only the `techo5_satellite_manager` component folder out of `custom_components`, then start HA again. Preserve your original configuration and SSH access. This version makes no satellite changes to undo.

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
