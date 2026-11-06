# CPU core availability

The optional **Keep all CPU cores online** configuration switch requests a minimum of four online cores through the existing MediaTek HPS manager. It defaults OFF for every satellite, independent of metrics and logging. **CPU core policy** reports observed status, online CPUs, requested minimum, thermal limit, error and pending restoration. Normal checks inspect CPU status even while the switch is off; inspection does not change the policy.

The control requires a TECHO5 marker, exactly four present cores and an active, validated HPS interface. ARM64 Linux 4.9 uses `num_base_custom1`; ARMv7 Linux 3.18 uses `num_base_perf_serv`. The corrected inspector has been checked read-only on Dot, Spot, Show 5 and Show 8 devices with these interfaces. Other profiles report unsupported without changing CPU settings. Actual device reboot recovery remains unverified.

Enabling saves the current minimum with the kernel boot ID in a protected `/run/ha-satellite-cpu-policy/baseline` file, then requests minimum four. Home Assistant persists the choice and checks it every 60 seconds. Following a reboot, restoration happens after HA and SSH are available, on a monitoring cycle; this is not an early boot hook. It requires Home Assistant to remain running. No firmware, root mount, startup file, CPU frequency, scheduler affinity or thermal limit is changed.

Disabling restores the minimum saved for the same boot. An offline disable remains OFF in HA with restoration pending until the satellite returns. A baseline from a different boot is discarded without writing its old value. If another actor changed the minimum, restoration is refused for review. Missing a saved baseline means there is no prior integration change to undo. Removing the integration does not restore a satellite: turn the switch OFF and verify restoration before removing it.

Version 0.5.1 preserves protected v0.5.0 baselines when restoring or migrating them, without changing `/data` ownership or permissions. New baselines live in RAM and disappear on reboot, when the previous runtime CPU policy also resets.

An already configured minimum of four is left alone; switching OFF does not undo a pre-existing manual policy. The control raises minimum availability, not a mandatory online mask: thermal and power safety can still reduce online cores. A constrained state is reported rather than overriding limits. Higher availability can increase power consumption and heat.

A lower aggregate CPU percentage after enabling cores can reflect a larger denominator. Compare total core usage and voice latency under equivalent workloads before claiming reduced work or improved performance.

Validation includes synthetic HA lifecycle tests and Linux shell tests for repeated enable, restoration, changed boot IDs, thermal constraints, external-policy conflicts, unsupported interfaces, read-only inspection and unsafe baseline paths. The corrected helper was exercised directly in bounded enable/readback/restore trials on a Dot and Show 5; original minimums were restored. All inspected device families passed read-only interface checks. The corrected HA adapter still needs operator installation. Vendor proc writes are accepted only when their readback matches the requested value, even if the write itself reports an error. Actual device reboot recovery remains unverified.
