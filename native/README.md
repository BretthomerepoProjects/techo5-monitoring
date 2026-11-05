# Native log streamer

`streamer.c` is the single-process Linux sender used by the integration. `test_streamer.c` exercises it with synthetic files, pipes and local sockets. It does not read real logs or contact satellites.

## Build

Use an isolated Python environment with `ziglang==0.13.0`, then run:

```sh
python tools/build_streamer.py
```

The script builds static musl ARMv7 Cortex-A7 and ARM64 Linux payloads under `dist/`, strips them and verifies their SHA256 values against the reviewed integration pins. A changed binary requires a reviewed source/build change and new pins; never replace a released payload in place. No compiler runs on Home Assistant or a satellite. Executables are release assets, not tracked source files or contents of the integration ZIP.

For synthetic Linux native tests with a local C compiler:

```sh
cc -std=c11 -O2 -UNDEBUG -Wall -Wextra -Werror -Wno-misleading-indentation native/test_streamer.c -o /tmp/t5sm-native-test
/tmp/t5sm-native-test
```

Assertions must be enabled. The tests use temporary files owned by the test account; the production sender requires root-owned source files. Run the Python/isolated shell suite separately with `python -m unittest discover -s tests -v`.

Third-party runtime/build notices are in `third-party/` and accompany the HA component as `streamer-NOTICES.txt`. They do not select a license for this project's original source; the repository's existing experimental licensing status applies.

See [operational setup and limitations](../docs/log-streamer.md) before use. The sender has fixed memory queues and bounded work per pass, but a permanently unavailable collector can still cause source-buffer loss. It is not a lossless journal or audit service.
