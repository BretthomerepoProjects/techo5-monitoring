set -eu
fail() { printf 'node_error=%s\n' "$1"; exit 1; }
unsupported() {
    printf 'cpu_policy_state=unsupported\ncpu_online=unknown\ncpu_minimum=unknown\ncpu_thermal_limit=unknown\n'
    exit 0
}
case "$1" in inspect|enable|disable) ;; *) fail invalid_action;; esac
[ "$(id -u)" = 0 ] || fail root_required
[ -f /etc/techo5/boot.sh ] || unsupported
# This control is for the four-core MediaTek HPS interface, not arbitrary CPUs.
[ "$(uname -m)" = aarch64 ] || unsupported
case "$(uname -r)" in 4.9.*) ;; *) unsupported;; esac
[ "$(cat /sys/devices/system/cpu/present)" = 0-3 ] || unsupported
for f in enabled num_base_custom1 num_limit_thermal num_limit_power_serv num_limit_custom1 num_limit_custom2; do
    [ -r /proc/hps/$f ] || unsupported
done
[ "$(cat /proc/hps/enabled)" = 1 ] || unsupported
for f in num_base_custom1 num_limit_thermal num_limit_power_serv num_limit_custom1 num_limit_custom2; do
    case "$(cat /proc/hps/$f)" in 1|2|3|4) ;; *) unsupported;; esac
done
base=/data/ha-satellite-manager
dir=$base/cpu-policy
boot=$(cat /proc/sys/kernel/random/boot_id)
case "$boot" in ''|*[!a-f0-9-]*) fail cpu_policy_conflict;; esac
safe_dir() {
    [ ! -L "$1" ] && [ -d "$1" ] || fail unsafe_path
    [ "$(stat -c %u "$1")" = 0 ] || fail unsafe_path
    case "$(stat -c %a "$1")" in 700|750|755) ;; *) fail unsafe_path;; esac
}
if [ "$1" != inspect ]; then
    umask 077
    safe_dir /data
    [ -e "$base" ] || mkdir "$base"
    safe_dir "$base"
    [ -e "$dir" ] || mkdir "$dir"
    safe_dir "$dir"
    mkdir /run/techo5-cpu-policy.lock 2>/dev/null || fail remote_busy
    trap 'rmdir /run/techo5-cpu-policy.lock' EXIT
fi
old_boot=none
old_min=none
if [ -e "$dir/baseline" ] || [ -L "$dir/baseline" ]; then
    safe_dir "$base"
    safe_dir "$dir"
    [ ! -L "$dir/baseline" ] && [ -f "$dir/baseline" ] || fail unsafe_path
    [ "$(stat -c %u:%a "$dir/baseline")" = 0:600 ] || fail unsafe_path
    [ "$(wc -c < "$dir/baseline")" -le 64 ] || fail cpu_policy_conflict
    read -r old_boot old_min < "$dir/baseline"
    case "$old_min" in 1|2|3|4) ;; *) fail cpu_policy_conflict;; esac
    case "$old_boot" in ''|*[!a-f0-9-]*) fail cpu_policy_conflict;; esac
fi
minimum=$(cat /proc/hps/num_base_custom1)
case "$1" in
enable)
    if [ "$old_boot" != "$boot" ]; then
        [ ! -e "$dir/baseline.new" ] && [ ! -L "$dir/baseline.new" ] || fail unsafe_path
        printf '%s %s\n' "$boot" "$minimum" > "$dir/baseline.new"
        mv "$dir/baseline.new" "$dir/baseline"
    elif [ "$minimum" != 4 ] && [ "$minimum" != "$old_min" ]; then
        fail cpu_policy_conflict
    fi
    [ "$minimum" = 4 ] || printf '4\n' > /proc/hps/num_base_custom1
    # Give HPS time to react; never force cores or bypass its safety limits.
    sleep 2
    ;;
disable)
    if [ "$old_boot" = "$boot" ]; then
        if [ "$minimum" != 4 ] && [ "$minimum" != "$old_min" ]; then fail cpu_policy_conflict; fi
        printf '%s\n' "$old_min" > /proc/hps/num_base_custom1
        [ "$(cat /proc/hps/num_base_custom1)" = "$old_min" ] || fail cpu_policy_write_failed
    fi
    rm -f "$dir/baseline"
    ;;
esac
minimum=$(cat /proc/hps/num_base_custom1)
online=$(cat /sys/devices/system/cpu/online)
thermal=$(cat /proc/hps/num_limit_thermal)
state=automatic
if [ "$minimum" = 4 ]; then
    state=requested
    [ "$online" != 0-3 ] || state=all_online
    for f in num_limit_thermal num_limit_power_serv num_limit_custom1 num_limit_custom2; do
        [ "$(cat /proc/hps/$f)" = 4 ] || state=constrained
    done
fi
[ "$1" != enable ] || [ "$minimum" = 4 ] || fail cpu_policy_write_failed
printf 'cpu_policy_state=%s\ncpu_online=%s\ncpu_minimum=%s\ncpu_thermal_limit=%s\n' "$state" "$online" "$minimum" "$thermal"
