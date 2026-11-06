# CPU core availability

The optional **Keep all CPU cores online** configuration switch requests a minimum of four online cores through the existing MediaTek HPS manager. It defaults OFF for every satellite, independent of metrics and logging. **CPU core policy** reports observed status, online CPUs, requested minimum, thermal limit, error and pending restoration.

The control requires a TECHO5 marker, ARM64 Linux 4.9, exactly four present cores and the expected active HPS interface. Other profiles report unsupported without changing CPU settings. Only a compatible Spot runtime has been manually observed; broader device compatibility and actual reboot acceptance remain unverified.

Enabling saves the current minimum with the kernel boot ID in a protected `/data/ha-satellite-manager/cpu-policy/baseline` file, then requests minimum four. Home Assistant persists the choice and checks it every 60 seconds. Following a reboot, restoration happens after HA and SSH are available, on a monitoring cycle; this is not an early boot hook. It requires Home Assistant to remain running. No firmware, root mount, startup file, CPU frequency, scheduler affinity or thermal limit is changed.

Disabling restores the minimum saved for the same boot. An offline disable remains OFF in HA with restoration pending until the satellite returns. A baseline from a different boot is discarded without writing its old value. If another actor changed the minimum, restoration is refused for review. Missing a saved baseline means there is no prior integration change to undo. Removing the integration does not restore a satellite: turn the switch OFF and verify restoration before removing it.

An already configured minimum of four is left alone; switching OFF does not undo a pre-existing manual policy. The control raises minimum availability, not a mandatory online mask: thermal and power safety can still reduce online cores. A constrained state is reported rather than overriding limits. Higher availability can increase power consumption and heat.

A lower aggregate CPU percentage after enabling cores can reflect a larger denominator. Compare total core usage and voice latency under equivalent workloads before claiming reduced work or improved performance.

Validation includes synthetic HA lifecycle tests and Linux shell tests for repeated enable, restoration, changed boot IDs, thermal constraints, external-policy conflicts, unsupported interfaces, read-only inspection and unsafe baseline paths. This experimental feature has not been installed into production HA. Actual device reboot recovery and broader device acceptance remain unverified.
