#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'debian platform FAIL: %s\n' "$1" >&2
  exit 1
}

[[ -r /etc/os-release ]] || fail '/etc/os-release missing'
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == 'debian' ]] || fail 'OS must be Debian'
[[ "${VERSION_ID:-}" == '12' ]] || fail 'Debian major version must be 12'
[[ "${VERSION_CODENAME:-}" == 'bookworm' ]] || fail 'Debian codename must be bookworm'

architecture=$(uname -m)
[[ "$architecture" == 'aarch64' ]] || fail 'architecture must be aarch64'

kernel=$(uname -r)
kernel_version=${kernel%%-*}
[[ "$(printf '%s\n' '6.1' "$kernel_version" | sort -V | head -1)" == '6.1' ]] \
  || fail 'kernel must be 6.1 or later'

[[ "$(stat -fc %T /sys/fs/cgroup)" == 'cgroup2fs' ]] || fail 'cgroup v2 required'

cpu_count=$(nproc)
[[ "$cpu_count" == '2' ]] || fail 'exactly 2 vCPU required'

memory_kib=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
[[ "$memory_kib" -ge 11534336 && "$memory_kib" -le 13631488 ]] \
  || fail 'memory must remain in the approved 11-13 GiB envelope'

root_bytes=$(df -B1 --output=size / | tail -1 | tr -d ' ')
[[ "$root_bytes" -ge 180000000000 && "$root_bytes" -le 220000000000 ]] \
  || fail 'root filesystem must remain in the approved 180-220 GB envelope'

[[ -r /sys/class/dmi/id/chassis_asset_tag ]] || fail 'OCI chassis identity missing'
[[ "$(tr -d '\n' </sys/class/dmi/id/chassis_asset_tag)" == 'OracleCloud.com' ]] \
  || fail 'host must be an Oracle Cloud instance'

systemd_version=$(systemctl --version | awk 'NR == 1 {print $2}')
[[ "$systemd_version" -ge 252 ]] || fail 'systemd 252 or later required'

docker_arch=$(docker info --format '{{.Architecture}}')
[[ "$docker_arch" == 'aarch64' ]] || fail 'Docker server architecture mismatch'
[[ "$(docker info --format '{{.CgroupVersion}}')" == '2' ]] \
  || fail 'Docker must use cgroup v2'
docker compose version >/dev/null || fail 'Docker Compose unavailable'

chrony_tracking=$(chronyc tracking)
grep -Eq '^Leap status[[:space:]]*:[[:space:]]*Normal$' <<<"$chrony_tracking" \
  || fail 'chrony leap status is not normal'

nft --version >/dev/null || fail 'nftables unavailable'

printf 'debian platform PASS os=12 codename=bookworm arch=%s kernel=%s cgroup=v2 cpu=%s memory_kib=%s root_bytes=%s provider=oci\n' \
  "$architecture" "$kernel" "$cpu_count" "$memory_kib" "$root_bytes"
