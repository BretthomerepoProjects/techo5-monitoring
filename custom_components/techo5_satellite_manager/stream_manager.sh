#!/bin/sh
# Trusted local manager streamed via strict SSH; no firmware or network-rule edits.
set -eu
umask 077
BASE=/data/ha-satellite-manager
ROOT=$BASE/streamer
RUN=/run/t5sm-streamer
action=$1 host=$2 collector=$3 port=$4 arch=$5 expected=$6 size=$7
fail() { printf 'node_error=%s\n' "$1"; exit 1; }
[ "$(id -u)" = 0 ] || fail root_required
[ -f /etc/techo5/boot.sh ] || fail unsupported_firmware
[ "$(uname -m)" = "$arch" ] || fail architecture_changed
case "$arch:$(uname -r)" in armv7l:3.18.*) profile=dot;; aarch64:4.9.*) profile=screen;; *) fail unsupported_firmware;; esac
case "$action" in inspect|deploy|start|stop|uninstall) ;; *) fail invalid_action;; esac
for tool in stat sha256sum readlink awk pidof date setsid; do command -v "$tool" >/dev/null || fail missing_tools; done
for dir in /data /run "$BASE" "$ROOT" "$RUN"; do [ ! -L "$dir" ] || fail unsafe_path; done
for dir in "$BASE" "$ROOT" "$RUN"; do
 if [ -e "$dir" ]; then [ -d "$dir" ] && [ "$(stat -c '%u:%a' "$dir")" = 0:700 ] || fail unsafe_path; fi
done
locked=no started=no success=no
bin=$ROOT/t5-log-stream
valid_pid() {
 [ -f "$ROOT/pid" ] || return 1
 read pid birth < "$ROOT/pid" || return 1
 case "$pid:$birth" in *[!0-9:]*|:*|*:) return 1;; esac
 [ "$pid" -gt 1 ] && [ -r "/proc/$pid/stat" ] || return 1
 [ "$(readlink "/proc/$pid/exe")" = "$bin" ] || return 1
 [ "$(awk '{print $22}' "/proc/$pid/stat")" = "$birth" ] || return 1
 [ "$(sha256sum "/proc/$pid/exe" | awk '{print $1}')" = "$expected" ]
}
cleanup() {
 rc=$?; trap - EXIT HUP INT TERM
 if [ "$success:$started" = no:yes ] && valid_pid; then kill -TERM "$pid" 2>/dev/null || true; fi
 rm -f "$ROOT/payload.tmp"
 [ "$locked" = no ] || rmdir "$BASE/operation.lock" 2>/dev/null || true
 exit "$rc"
}
if [ "$action" != inspect ]; then
 mkdir -p "$BASE" "$ROOT"
 mkdir "$BASE/operation.lock" 2>/dev/null || fail remote_busy
 locked=yes; trap cleanup EXIT; trap 'exit 1' HUP INT TERM
fi
for file in "$ROOT/config" "$ROOT/pid" "$bin" "$ROOT/previous" "$ROOT/payload.tmp" "$RUN/status" "$RUN/status.tmp"; do
 [ ! -L "$file" ] || fail unsafe_path
 if [ -e "$file" ]; then
  [ -f "$file" ] && [ "$(stat -c '%u:%h' "$file")" = 0:1 ] || fail unsafe_path
  case "$(stat -c '%a' "$file")" in 600|700) ;; *) fail unsafe_path;; esac
 fi
done
installed=no
if [ -e "$bin" ]; then
 if [ -x "$bin" ] && [ "$(sha256sum "$bin" | awk '{print $1}')" = "$expected" ]; then installed=yes; else installed=invalid; fi
fi
saved_collector=$collector saved_port=$port saved_profile=$profile
if [ -f "$ROOT/config" ]; then
 read saved_collector saved_port saved_profile saved_hash < "$ROOT/config"
 [ "$saved_hash" = "$expected" ] && [ "$saved_profile" = "$profile" ] || fail invalid_config
 printf '%s\n' "$saved_collector" | awk -F. 'NF!=4 {exit 1} {for(i=1;i<=4;i++)if($i!~/^[0-9]+$/||$i>255)exit 1}' || fail invalid_config
 case "$saved_port" in ''|*[!0-9]*) fail invalid_config;; esac
 [ "$saved_port" -ge 1024 ] && [ "$saved_port" -le 65535 ] || fail invalid_config
elif [ "$installed" != no ]; then fail invalid_config; fi
owned=no;valid_pid && owned=yes
state=not_installed
case "$installed" in yes) state=stopped;; invalid) state=invalid;; esac
[ "$owned" = no ] || state=running
for p in $(pidof t5-log-stream 2>/dev/null || true); do
 if [ "$owned" = no ] || [ "$p" != "$pid" ]; then state=unmanaged; fi
done
report() {
 printf 'stream_state=%s\nstream_profile=%s\nstream_version=0.1.0\n' "$state" "$profile"
 # Only allowlisted scalar counters, never source records, enter HA attributes.
 if [ "$state" = running ] && [ -f "$RUN/status" ] && [ "$(stat -c '%s' "$RUN/status")" -le 2048 ]; then
  awk -F= 'NF==2 && $2~/^[0-9]+$/ && $1~/^(connected|written|dropped|truncated|kernel_lost|kernel_records|read_errors|files|kernel_open|queue|uptime|cpu_us|file_gaps)$/ {print "stream_"$1"="$2}' "$RUN/status"
 fi
}
if [ "$action" = inspect ]; then report;exit 0;fi
[ "$state" != unmanaged ] || fail unmanaged_streamer
if [ -r /etc/inittab ] && grep -q '^[^#].*t5-log-stream' /etc/inittab; then fail external_supervisor; fi
case "$action" in deploy|start)
 [ -r /dev/kmsg ] && [ -c /dev/kmsg ] || fail kernel_log_unavailable
 if [ "$profile" = dot ]; then app=/data/techo5-linux/echod.log; else app=/data/techo5-linux/techo5.log; fi
 [ -f "$app" ] && [ ! -L "$app" ] && [ -r "$app" ] || fail application_log_unavailable
 if [ "$saved_collector $saved_port" != "$collector $port" ]; then
  [ "$owned" = no ] && [ "$action" = deploy ] || fail configuration_changed_stop_first
 fi
 if [ "$action" = deploy ] && [ "$installed" != yes ]; then
  [ "$size" -gt 0 ] && [ "$size" -le 2097152 ] || fail invalid_payload_size
  free=$(df -Pk /data | awk 'END{print $4}');[ "$free" -ge 8192 ] || fail insufficient_storage
  (ulimit -f 4096;cat > "$ROOT/payload.tmp") || fail upload_failed
  [ "$(wc -c < "$ROOT/payload.tmp" | tr -d ' ')" = "$size" ] || fail incomplete_upload
  [ "$(sha256sum "$ROOT/payload.tmp" | awk '{print $1}')" = "$expected" ] || fail binary_checksum_failed
  chmod 700 "$ROOT/payload.tmp"
  if [ "$installed" = invalid ]; then
   [ "$(stat -c '%s' "$bin")" -le 2097152 ] || fail invalid_payload_size
   mv "$bin" "$ROOT/previous"
  fi
  mv "$ROOT/payload.tmp" "$bin";installed=yes
 fi
 [ "$installed" = yes ] || fail stream_requires_redeploy
 printf '%s %s %s %s\n' "$collector" "$port" "$profile" "$expected" > "$ROOT/config"
 if [ "$owned" = no ]; then
  mkdir -p "$RUN"
  rm -f "$RUN/status" "$RUN/status.tmp"
  setsid "$bin" "$collector" "$port" "$host" "$profile" "$RUN/status" </dev/null >/dev/null 2>&1 &
  pid=$!
  [ -r "/proc/$pid/stat" ] || fail start_failed
  printf '%s %s\n' "$pid" "$(awk '{print $22}' "/proc/$pid/stat")" > "$ROOT/pid"
  started=yes;sleep 2;valid_pid || fail start_failed
 fi
 state=running
 ;;
 stop|uninstall)
 if [ "$owned" = yes ]; then
  valid_pid || fail process_changed;kill -TERM "$pid"
  n=0;while valid_pid && [ "$n" -lt 8 ]; do sleep 1;n=$((n+1));done
  valid_pid && fail stop_failed
 fi
 rm -f "$ROOT/pid" "$RUN/status" "$RUN/status.tmp"
 state=not_installed
 case "$installed" in yes) state=stopped;; invalid) state=invalid;; esac
 if [ "$action" = uninstall ]; then
  rm -f "$bin" "$ROOT/previous" "$ROOT/config"
  rmdir "$ROOT" || fail unexpected_files
  state=not_installed
 fi
 ;;
esac
success=yes
report
