# Public integration development

This repository is public. Treat every commit, branch, issue, test output and release artifact as public content.

- Include reusable integration source, synthetic tests, generic documentation and build tooling only.
- Never commit private or public SSH keys, host-key files, passwords, tokens, passphrases, local credential paths, Home Assistant configuration exports, raw logs, audio, captures, device inventories or backups.
- Configure addresses and credential filenames locally. Do not hard-code a contributor's hostnames, usernames, network addresses, MAC addresses or household details.
- Test fixtures must be synthetic. Generate ephemeral SSH keys inside temporary test directories; never save them as fixtures or upload them as CI artifacts.
- Run the publication guard against the staged files and inspect the complete staged diff before every commit. Ignore rules alone do not protect already tracked files or history.
- Keep experimental deployment/recovery work in a separate branch. Do not describe untested controls as working or automatically deploy updates into production.
- Preserve strict host-key verification, bounded asynchronous operations, administrator authorization and reversible activation.
- This software is not upstream TECHO5 firmware. Do not assume firmware compatibility from CPU architecture alone.
- Never perform live deployment, restart or remote service changes from CI. CI does not need access to any real satellite or Home Assistant instance.
- Choose and document a license before presenting this as a reusable open-source release. No license has been selected yet.
