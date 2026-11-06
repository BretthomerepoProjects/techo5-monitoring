set -eu
fail() { printf 'node_error=%s\n' "$1"; exit 1; }
unsupported() {
    printf 'cpu_policy_state=unsupported\ncpu_online=unknown\ncpu_minimum=unknown\ncpu_thermal_limit=unknown\n'
    exit 0
}
case "$1" in inspect|enable|disable) ;; *) fail invalid_action;; esac
[ "$(id -u)" = 0 ] || fail root_required
[ -f /etc/techo5/boot.sh ] || unsupported
# Match the actual HPS interface as well as architecture and kernel family.
case "$(uname -m):$(uname -r)" in
    aarch64:4.9.*)
        minimum_file=num_base_custom1
        limits="num_limit_thermal num_limit_power_serv num_limit_custom1 num_limit_custom2 num_limit_low_battery num_limit_ultra_power_saving"
        ;;
    armv7l:3.18.*)
        minimum_file=num_base_perf_serv
        limits="num_limit_thermal num_limit_power_serv num_limit_low_battery num_limit_ultra_power_saving"
        ;;
    *) unsupported;;
esac
[ "$(cat /sys/devices/system/cpu/present)" = 0-3 ] || unsupported
for f in enabled "$minimum_file" $limits; do
    [ -r /proc/hps/$f ] || unsupported
done
[ "$(cat /proc/hps/enabled)" = 1 ] || unsupported
for f in "$minimum_file" $limits; do
    case "$(cat /proc/hps/$f)" in 1|2|3|4) ;; *) unsupported;; esac
done
# Original minimum is per boot. RAM storage avoids assumptions about /data ownership.
dir=/run/ha-satellite-cpu-policy
legacy=/data/ha-satellite-manager/cpu-policy
boot=$(cat /proc/sys/kernel/random/boot_id)
case "$boot" in ''|*[!a-f0-9-]*) fail cpu_policy_conflict;; esac
safe_dir() {
    [ ! -L "$1" ] && [ -d "$1" ] || fail unsafe_path
    [ "$(stat -c %u "$1")" = 0 ] || fail unsafe_path
    case "$(stat -c %a "$1")" in 700|750|755) ;; *) fail unsafe_path;; esac
}
if [ "$1" != inspect ]; then
    umask 077
    [ ! -L /run ] && [ -d /run ] && [ "$(stat -c %u /run)" = 0 ] || fail unsafe_path
    case "$(stat -c %a /run)" in 700|750|755|1777) ;; *) fail unsafe_path;; esac
    [ -e "$dir" ] || mkdir "$dir"
    safe_dir "$dir"
    mkdir "$dir/lock" 2>/dev/null || fail remote_busy
    trap 'rmdir "$dir/lock"' EXIT
fi
old_boot=none
old_min=none
baseline=$dir/baseline
legacy_used=no
if [ ! -e "$baseline" ] && [ ! -L "$baseline" ] && { [ -e "$legacy/baseline" ] || [ -L "$legacy/baseline" ]; }; then
    # v0.5.0 baselines are accepted only through the original protected path.
    safe_dir /data
    safe_dir /data/ha-satellite-manager
    safe_dir "$legacy"
    baseline=$legacy/baseline
    legacy_used=yes
fi
if [ -e "$baseline" ] || [ -L "$baseline" ]; then
    if [ "$legacy_used" = no ]; then safe_dir "$dir"; fi
    [ ! -L "$baseline" ] && [ -f "$baseline" ] || fail unsafe_path
    [ "$(stat -c %u:%a "$baseline")" = 0:600 ] || fail unsafe_path
    [ "$(wc -c < "$baseline")" -le 64 ] || fail cpu_policy_conflict
    read -r old_boot old_min extra < "$baseline"
    [ -z "$extra" ] || fail cpu_policy_conflict
    case "$old_min" in 1|2|3|4) ;; *) fail cpu_policy_conflict;; esac
    case "$old_boot" in ''|*[!a-f0-9-]*) fail cpu_policy_conflict;; esac
fi
set_minimum() {
    # Some vendor proc handlers report a short/error write after applying it.
    # Success is determined by readback, never by the write return alone.
    printf '%s' "$1" > /proc/hps/$minimum_file 2>/dev/null || :
    [ "$(cat /proc/hps/$minimum_file)" = "$1" ] || fail cpu_policy_write_failed
}
minimum=$(cat /proc/hps/$minimum_file)
case "$1" in
enable)
    if [ "$old_boot" = "$boot" ] && [ "$minimum" != 4 ] && [ "$minimum" != "$old_min" ]; then fail cpu_policy_conflict; fi
    if [ "$old_boot" != "$boot" ] || [ "$legacy_used" = yes ]; then
        saved_min=$minimum
        if [ "$old_boot" = "$boot" ]; then saved_min=$old_min; fi
        [ ! -e "$dir/baseline.new" ] && [ ! -L "$dir/baseline.new" ] || fail unsafe_path
        printf '%s %s\n' "$boot" "$saved_min" > "$dir/baseline.new"
        mv "$dir/baseline.new" "$dir/baseline"
        if [ "$legacy_used" = yes ]; then rm "$legacy/baseline"; fi
    elif [ "$minimum" != 4 ] && [ "$minimum" != "$old_min" ]; then
        fail cpu_policy_conflict
    fi
    [ "$minimum" = 4 ] || set_minimum 4
    # Give HPS time to react; never force cores or bypass its safety limits.
    sleep 2
    ;;
disable)
    if [ "$old_boot" = "$boot" ]; then
        if [ "$minimum" != 4 ] && [ "$minimum" != "$old_min" ]; then fail cpu_policy_conflict; fi
        [ "$minimum" = "$old_min" ] || set_minimum "$old_min"
    fi
    rm -f "$baseline"
    ;;
esac
minimum=$(cat /proc/hps/$minimum_file)
online=$(cat /sys/devices/system/cpu/online)
thermal=$(cat /proc/hps/num_limit_thermal)
state=automatic
if [ "$minimum" = 4 ]; then
    state=requested
    [ "$online" != 0-3 ] || state=all_online
    for f in $limits; do
        [ "$(cat /proc/hps/$f)" = 4 ] || state=constrained
    done
fi
[ "$1" != enable ] || [ "$minimum" = 4 ] || fail cpu_policy_write_failed
printf 'cpu_policy_state=%s\ncpu_online=%s\ncpu_minimum=%s\ncpu_thermal_limit=%s\n' "$state" "$online" "$minimum" "$thermal"
