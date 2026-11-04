#!/bin/sh
# Run from the authenticated SSH command, never source remote configuration.
set -eu
umask 077
BASE=/data/ha-satellite-manager
ROOT=$BASE/node
action=$1 host=$2 collector=$3 port=$4 arch=$5 expected=$6 size=$7
fail() { printf 'node_error=%s\n' "$1"; exit 1; }
[ "$(id -u)" = 0 ] || fail root_required
[ -f /etc/techo5/boot.sh ] || fail unsupported_firmware
[ "$(uname -m)" = "$arch" ] || fail architecture_changed
case "$arch:$(uname -r)" in armv7l:3.18.*|aarch64:4.9.*) ;; *) fail unsupported_firmware;; esac
case "$action" in inspect|deploy|start|stop|uninstall) ;; *) fail invalid_action;; esac
for tool in stat sha256sum readlink awk pidof date; do command -v "$tool" >/dev/null || fail missing_tools; done
# Reject symlinked ancestors and unexpected writable installation paths.
for dir in /data "$BASE" "$ROOT"; do
  [ ! -L "$dir" ] || fail unsafe_path
done
for dir in "$BASE" "$ROOT"; do
  if [ -e "$dir" ]; then
    [ -d "$dir" ] && [ "$(stat -c '%u:%a' "$dir")" = 0:700 ] || fail unsafe_path
  fi
done
started=no added_allow=no added_deny=no success=no
cleanup() {
  rc=$?
  trap - EXIT HUP INT TERM
  if [ "$success" = no ]; then
    if [ "$started" = yes ] && valid_pid; then kill -TERM "$pid" 2>/dev/null || true; fi
    if [ "$added_allow" = yes ]; then rule allow -D 2>/dev/null || true; fi
    if [ "$added_deny" = yes ]; then rule deny -D 2>/dev/null || true; fi
  fi
  rm -f "$ROOT/payload.tmp"
  rmdir "$BASE/operation.lock" 2>/dev/null || true
  exit "$rc"
}
if [ "$action" != inspect ]; then
  mkdir -p "$BASE" "$ROOT"
  mkdir "$BASE/operation.lock" 2>/dev/null || fail remote_busy
  trap cleanup EXIT
  trap 'exit 1' HUP INT TERM
fi
for file in node_exporter node_exporter.previous config pid firewall.before firewall_mode payload.tmp; do
  [ ! -L "$ROOT/$file" ] || fail unsafe_path
  if [ -e "$ROOT/$file" ]; then
    [ -f "$ROOT/$file" ] && [ "$(stat -c '%u' "$ROOT/$file")" = 0 ] || fail unsafe_path
    case "$(stat -c '%a' "$ROOT/$file")" in 600|700) ;; *) fail unsafe_path;; esac
  fi
done
bin=$ROOT/node_exporter
installed=no
if [ -e "$bin" ]; then
  if [ "$(sha256sum "$bin" | awk '{print $1}')" = "$expected" ] && [ -x "$bin" ]; then installed=yes
  else installed=invalid; fi
fi
saved_host=$host saved_collector=$collector saved_port=$port
if [ -f "$ROOT/config" ]; then
  read saved_host saved_collector saved_port saved_hash < "$ROOT/config"
  [ "$saved_hash" = "$expected" ] || fail invalid_config
  # Configuration is data, never shell code; validate before firewall operations.
  for address in "$saved_host" "$saved_collector"; do
    printf '%s\n' "$address" | awk -F. 'NF!=4 {exit 1} {for(i=1;i<=4;i++) if($i!~/^[0-9]+$/ || $i>255) exit 1}' || fail invalid_config
  done
  case "$saved_port" in ''|*[!0-9]*) fail invalid_config;; esac
  [ "$saved_port" -ge 1024 ] && [ "$saved_port" -le 65535 ] || fail invalid_config
elif [ "$installed" != no ]; then fail invalid_config
fi
IPT=
for candidate in iptables-legacy iptables; do
  command -v "$candidate" >/dev/null || continue
  case "$("$candidate" --version 2>/dev/null)" in *nf_tables*) continue;; esac
  "$candidate" -S INPUT >/dev/null 2>&1 || continue
  IPT=$candidate; break
done
# Host-bound metrics always traverse INPUT. Insert before any firmware jump/ACCEPT.
chain=INPUT
firewall_mode=comment
rule() {
  kind=$1 op=$2
  shift 2
  if [ "$kind" = allow ]; then
    if [ "$firewall_mode" = comment ]; then
      "$IPT" "$op" "$chain" "$@" -s "$saved_collector/32" -d "$saved_host/32" -p tcp --dport "$saved_port" -m comment --comment t5sm-node-allow -j ACCEPT
    else
      "$IPT" "$op" "$chain" "$@" -s "$saved_collector/32" -d "$saved_host/32" -p tcp --dport "$saved_port" -j ACCEPT
    fi
  else
    if [ "$firewall_mode" = comment ]; then
      "$IPT" "$op" "$chain" "$@" -d "$saved_host/32" -p tcp --dport "$saved_port" -m comment --comment t5sm-node-deny -j DROP
    else
      "$IPT" "$op" "$chain" "$@" -d "$saved_host/32" -p tcp --dport "$saved_port" -j DROP
    fi
  fi
}
# Old installations used comment-tagged rules. Preserve their ownership, but new
# rules use a protected manifest so ARMv7 kernels need no xt_comment support.
claimed=no
if [ -f "$ROOT/firewall_mode" ]; then
  read firewall_mode < "$ROOT/firewall_mode"
  case "$firewall_mode" in plain|comment) ;; *) fail invalid_config;; esac
  claimed=yes
elif [ -n "$IPT" ] && { rule allow -C 2>/dev/null || rule deny -C 2>/dev/null; }; then
  claimed=yes
else
  firewall_mode=plain
fi
firewall=absent
if [ -n "$IPT" ]; then
  allow=no deny=no
  rule allow -C 2>/dev/null && allow=yes
  rule deny -C 2>/dev/null && deny=yes
  if [ "$allow:$deny" = yes:yes ]; then
    first=$("$IPT" -S "$chain" | awk 'NR==2 {gsub(/ -m tcp /," "); print}' | tr -d '"')
    second=$("$IPT" -S "$chain" | awk 'NR==3 {gsub(/ -m tcp /," "); print}' | tr -d '"')
    if [ "$firewall_mode" = comment ]; then
      case "$first:$second" in *'--comment t5sm-node-allow'*:*'--comment t5sm-node-deny'*) firewall=restricted;; *) firewall=partial;; esac
    elif [ "$claimed" = yes ] &&
      [ "$first" = "-A $chain -s $saved_collector/32 -d $saved_host/32 -p tcp --dport $saved_port -j ACCEPT" ] &&
      [ "$second" = "-A $chain -d $saved_host/32 -p tcp --dport $saved_port -j DROP" ]; then
      firewall=restricted
    else firewall=partial; fi
  elif [ "$allow:$deny" != no:no ]; then firewall=partial; fi
fi
valid_pid() {
  [ -f "$ROOT/pid" ] || return 1
  read pid birth < "$ROOT/pid" || return 1
  case "$pid:$birth" in *[!0-9:]*|:*) return 1;; esac
  [ "$pid" -gt 1 ] && [ -r "/proc/$pid/stat" ] || return 1
  [ "$(readlink "/proc/$pid/exe")" = "$bin" ] || return 1
  [ "$(awk '{print $22}' "/proc/$pid/stat")" = "$birth" ] || return 1
  [ "$(sha256sum "/proc/$pid/exe" | awk '{print $1}')" = "$expected" ]
}
owned=no
valid_pid && owned=yes
state=not_installed
case "$installed" in yes) state=stopped;; invalid) state=invalid;; esac
[ "$owned" = no ] || state=running
# Never kill/adopt an existing pilot, or fight firmware/init supervision.
for p in $(pidof node_exporter 2>/dev/null || true); do
  if [ "$owned" = no ] || [ "$p" != "$pid" ]; then state=unmanaged; fi
done
if [ "$action" = inspect ]; then
  printf 'node_state=%s\nnode_firewall=%s\nnode_version=1.12.1\n' "$state" "$firewall"
  exit 0
fi
[ "$state" != unmanaged ] || fail unmanaged_exporter
[ "$claimed:$firewall" != no:partial ] || fail firewall_requires_review
if [ -r /etc/inittab ] && grep -q '^[^#].*node[-_]exporter' /etc/inittab; then fail external_supervisor; fi
if [ -d /etc/init.d ]; then
  for file in /etc/init.d/*node*export*; do [ ! -e "$file" ] || fail external_supervisor; done
fi
[ -n "$IPT" ] || fail legacy_iptables_required
reconfigure=no
case "$action" in deploy|start)
  command -v setsid >/dev/null || fail missing_tools
  if [ "$owned" = no ]; then
    [ -r /proc/net/tcp ] || fail cannot_check_listener
    set -- /proc/net/tcp
    [ ! -r /proc/net/tcp6 ] || set -- "$@" /proc/net/tcp6
    hexport=$(printf '%04X' "$port")
    if awk -v want="$hexport" '$4=="0A" {split($2,a,":"); if(a[2]==want) found=1} END {exit !found}' "$@"; then
      fail exporter_port_in_use
    fi
  fi
  if [ "$saved_host $saved_collector $saved_port" != "$host $collector $port" ]; then
    if [ "$action:$owned:$firewall" = deploy:no:absent ]; then reconfigure=yes
    else fail configuration_changed_stop_first; fi
  fi
  ;;
esac
if [ "$reconfigure:$firewall_mode" = yes:plain ]; then
  original_host=$saved_host original_collector=$saved_collector original_port=$saved_port
  saved_host=$host saved_collector=$collector saved_port=$port
  if rule allow -C 2>/dev/null || rule deny -C 2>/dev/null; then fail firewall_requires_review; fi
  saved_host=$original_host saved_collector=$original_collector saved_port=$original_port
fi
# Bounded on-device rollback evidence: latest pre-change chain snapshot with UTC header.
(ulimit -f 512; date -u '+# %Y-%m-%dT%H:%M:%SZ'; "$IPT" -S "$chain") > "$ROOT/firewall.before" || fail firewall_backup_failed
if [ "$reconfigure" = yes ]; then
  saved_host=$host saved_collector=$collector saved_port=$port
  printf '%s %s %s %s\n' "$host" "$collector" "$port" "$expected" > "$ROOT/config"
fi
if [ "$action" = deploy ] && [ "$installed" != yes ]; then
  [ "$size" -gt 0 ] && [ "$size" -le 33554432 ] || fail invalid_payload_size
  free=$(df -Pk /data | awk 'END {print $4}')
  [ "$free" -ge 65536 ] || fail insufficient_storage
  # Limit a partial or interrupted upload to 32 MiB (POSIX 512-byte blocks).
  (ulimit -f 65536; cat > "$ROOT/payload.tmp") || fail upload_failed
  [ "$(wc -c < "$ROOT/payload.tmp" | tr -d ' ')" = "$size" ] || fail incomplete_upload
  [ "$(sha256sum "$ROOT/payload.tmp" | awk '{print $1}')" = "$expected" ] || fail binary_checksum_failed
  chmod 700 "$ROOT/payload.tmp"
  if [ "$installed" = invalid ]; then
    [ "$(stat -c '%s' "$bin")" -le 33554432 ] || fail invalid_payload_size
    mv "$bin" "$ROOT/node_exporter.previous"
  fi
  mv "$ROOT/payload.tmp" "$bin"
  printf '%s %s %s %s\n' "$host" "$collector" "$port" "$expected" > "$ROOT/config"
  installed=yes
fi
case "$action" in
  deploy|start)
    [ "$installed" = yes ] || fail not_installed
    [ "$firewall" != partial ] || fail firewall_requires_review
    printf '%s\n' "$firewall_mode" > "$ROOT/firewall_mode"
    # Insert before firmware DROP. Deny all other sources to this exact endpoint.
    if ! rule deny -C 2>/dev/null; then rule deny -I 1 || fail firewall_update_failed; added_deny=yes; fi
    if ! rule allow -C 2>/dev/null; then rule allow -I 1 || fail firewall_update_failed; added_allow=yes; fi
    if [ "$owned" = no ]; then
      setsid "$bin" --web.listen-address="$host:$port" --collector.disable-defaults \
        --collector.cpu --collector.meminfo --collector.loadavg --collector.netdev \
        --collector.filesystem --collector.uname --web.disable-exporter-metrics \
        </dev/null >/dev/null 2>&1 &
      pid=$!
      # Record ownership immediately; PID reuse must never authorize a later kill.
      [ -r "/proc/$pid/stat" ] || fail start_failed
      printf '%s %s\n' "$pid" "$(awk '{print $22}' "/proc/$pid/stat")" > "$ROOT/pid"
      started=yes
      sleep 2
      valid_pid || fail start_failed
    fi
    state=running firewall=restricted
    ;;
  stop|uninstall)
    if [ "$owned" = yes ]; then
      valid_pid || fail process_changed
      kill -TERM "$pid"
      n=0
      while valid_pid && [ "$n" -lt 8 ]; do sleep 1; n=$((n+1)); done
      if valid_pid; then fail stop_failed; fi
    fi
    if rule allow -C 2>/dev/null; then rule allow -D || fail firewall_update_failed; fi
    if rule deny -C 2>/dev/null; then rule deny -D || fail firewall_update_failed; fi
    rm -f "$ROOT/pid"
    state=not_installed
    case "$installed" in yes) state=stopped;; invalid) state=invalid;; esac
    firewall=absent
    if [ "$action" = uninstall ]; then
      rm -f "$bin" "$ROOT/node_exporter.previous" "$ROOT/config" "$ROOT/firewall.before" "$ROOT/firewall_mode"
      rmdir "$ROOT" || fail unexpected_files
      state=not_installed
    fi
    ;;
esac
success=yes
printf 'node_state=%s\nnode_firewall=%s\nnode_version=1.12.1\n' "$state" "$firewall"
