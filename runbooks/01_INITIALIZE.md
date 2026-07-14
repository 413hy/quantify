# 01 VPS 初始化运行手册

## 目的

把一台全新的 Ubuntu 24.04 LTS 韩国 VPS 准备成可运行 Testnet/Shadow 的受控环境。本手册不启用生产交易，不写入真实密钥；生产解锁另见 [04 实盘解锁](04_LIVE_ARMING.md)。架构和资源预算见 [部署设计](../docs/10_DOCKER_VPS_DEPLOYMENT.md)。

说明：文中的 `quantctl` 是实现阶段必须提供的受控 CLI 契约；在该 CLI、权限校验和审计实现前，这些示例仅用于开发验收，不能用临时脚本绕过。

主机 bootstrap 完成后、任何 Testnet 或生产公开 Binance 请求之前，必须先完整执行 [00 宿主级出站控制面](00_HOST_RATE_CONTROL.md)。`aiq-host-control` 使用独立卷和 database，后续业务项目不得接管其生命周期。

## 前置条件与人工门禁

- 账户所有者已书面确认实名账户、实际使用地区、Futures 产品/API 资格和当前条款允许该部署；不得以技术可达性代替合规判断。
- VPS 规格为 2 vCPU、12 GiB、约 200 GB NVMe，具有静态公网 IP；已准备独立回测机/加密 SFTP 归档端和足够的 90 天容量。
- 运维终端使用强 SSH key，已核验 VPS host fingerprint；主机时间使用 UTC。
- 发布包具备签名 release manifest、镜像 digest、SBOM、依赖锁、配置 Schema 和迁移版本。
- 此阶段只允许 Testnet secret；如发现生产 secret，立即停止并按 P0 安全事件处置。

## 1. 主机 bootstrap（全新 VPS 必做）

实现仓库必须随 release 提供 `deploy/host-toolchain.lock.yaml`、`deploy/host-hardening/` 和 `scripts/bootstrap-host.sh`。toolchain lock 至少固定 Ubuntu source/snapshot、Docker Engine/Compose、chrony、jq、age、cosign、数据库客户端、`quantctl` 安装包的版本、来源、签名指纹和 SHA-256；hardening 目录固定 sshd、nftables/ufw、Docker daemon、journald、chrony、sysctl 和 limits 配置摘要。禁止浮动 `latest`、未经摘要验证的二进制和 `curl | sh`。

bootstrap bundle 先在受信运维终端完成签名与 SHA-256 验证，再经已核验 host fingerprint 的 SSH/SFTP 上传；审批记录中的 bundle hash 必须经独立通道交叉核对。脚本必须支持无修改的 `plan` 和显式批准的 `apply`，所有动作可审计、可重复：

```bash
set -euo pipefail
export BOOTSTRAP_BUNDLE="<BOOTSTRAP_BUNDLE>"
export BOOTSTRAP_SHA256="<APPROVED_BOOTSTRAP_SHA256>"
export SSH_PORT="<APPROVED_SSH_PORT>"
printf '%s  %s\n' "$BOOTSTRAP_SHA256" "$BOOTSTRAP_BUNDLE" | sha256sum --check -
tar --extract --zstd --file "$BOOTSTRAP_BUNDLE" --directory "<BOOTSTRAP_STAGING_DIR>"
cd "<BOOTSTRAP_STAGING_DIR>"
./scripts/bootstrap-host.sh plan \
  --toolchain-lock deploy/host-toolchain.lock.yaml \
  --hardening-dir deploy/host-hardening \
  --ssh-port "$SSH_PORT" \
  --output "<BOOTSTRAP_PLAN>"
sha256sum "<BOOTSTRAP_PLAN>"
read -r -p "输入 BOOTSTRAP-<PLAN_SHA256_PREFIX> 继续: " CONFIRM
test "$CONFIRM" = "BOOTSTRAP-<PLAN_SHA256_PREFIX>" || exit 1
sudo ./scripts/bootstrap-host.sh apply --plan "<BOOTSTRAP_PLAN>" --approval "<SIGNED_BOOTSTRAP_APPROVAL>"
sudo ./scripts/bootstrap-host.sh verify --plan "<BOOTSTRAP_PLAN>" --output "<BOOTSTRAP_EVIDENCE>"
```

`plan/apply/verify` 必须完成并证明：

- 创建独立 `aiqops` 运维用户和非登录 `aiqsvc` 服务 UID/组，建立 `/srv/ai-quant`、`/etc/ai-quant`、日志和证据目录，按最小权限分配所有权；不得把运维用户加入可无审计任意提权的路径。
- 新运维用户的 SSH key 登录在**第二个会话**验证成功后，才切换到 key-only、禁密码、禁 root 远程登录、限制来源 CIDR；`sshd -t` 失败时不重载，始终保留云厂商控制台恢复路径。
- 主机防火墙默认拒绝入站，只允许审批的 SSH 来源/端口；未开放 8080、9090、9093、5432、6379。出站策略按签名 egress policy 配置，维护目的地默认关闭。
- 固定 UTC、安装并配置多源 chrony；配置 Docker 官方签名源、固定 Engine/Compose 版本、`/srv/ai-quant/docker` 数据根、有界 json-file/journald 轮转、`live-restore` 和最小 daemon 权限。
- 应用签名锁中的 sysctl、文件句柄、socket backlog、TCP keepalive 和服务 limits；不得关闭 AppArmor、seccomp 或内核安全更新。自动更新只能在维护窗口按签名变更执行。
- 安装并校验 jq、age、cosign、`quantctl` 等锁定工具；保存软件包版本、仓库签名、文件摘要、配置 diff、用户/组、目录权限、防火墙规则、监听端口和回滚说明。

任何工具摘要、SSH 第二会话、防火墙、时间同步或 verify 失败都停止；不得进入下面的部署预检。

## 2. 部署预检与安全命令

以下命令由运维人员在目标 VPS 上逐项执行；尖括号值必须显式替换。先做只读核验：

```bash
set -euo pipefail
export PROJECT_DIR="<PROJECT_DIR>"
export TESTNET_ENV_PATH="<TESTNET_ENV_PATH>"
cd "$PROJECT_DIR"
DC=(docker compose -p aiq-testnet -f deploy/compose.yaml --env-file "$TESTNET_ENV_PATH")
uname -a
lsb_release -a
nproc
free -h
df -hT
timedatectl status
chronyc tracking
ss -lntup
docker version
docker compose version
```

验证 release 和 Compose，不启动服务：

```bash
cd "$PROJECT_DIR"
sha256sum --check "<RELEASE_MANIFEST_CHECKSUM_FILE>"
"${DC[@]}" --profile testnet config --quiet
"${DC[@]}" --profile testnet config --images
RENDERED_COMPOSE="$(mktemp)"
trap 'rm -f "$RENDERED_COMPOSE"' EXIT
"${DC[@]}" --profile testnet config --format json > "$RENDERED_COMPOSE"
if grep --line-number --extended-regexp '"image"[[:space:]]*:[[:space:]]*"[^"]*:latest([@"]|[^"]*")' "$RENDERED_COMPOSE"; then
  echo "发现 latest 镜像引用" >&2
  exit 1
fi
if jq -e '[.services[]? | select(.image? != null) | .image | select(test("@sha256:[0-9a-f]{64}$") | not)] | length > 0' "$RENDERED_COMPOSE" >/dev/null; then
  echo "发现未以 sha256 digest 固定的镜像" >&2
  exit 1
fi
if jq -e '[.services[]?.ports[]? | select(if type != "object" then true else ((.published // "") != "" and (.host_ip // "") != "127.0.0.1" and (.host_ip // "") != "::1") end)] | length > 0' "$RENDERED_COMPOSE" >/dev/null; then
  echo "发现未显式绑定 127.0.0.1/::1 的发布端口" >&2
  exit 1
fi
if jq -e '[.services | to_entries[] | select((.value.network_mode // "") == "host" or (.value.pid // "") == "host" or (.value.ipc // "") == "host" or (.value.privileged // false) == true)] | length > 0' "$RENDERED_COMPOSE" >/dev/null; then
  echo "发现 host network/PID/IPC 或 privileged 服务" >&2
  exit 1
fi
quantctl compose security-verify \
  --rendered "$RENDERED_COMPOSE" \
  --deny-host-network --deny-host-pid --deny-host-ipc --deny-privileged \
  --deny-docker-socket --require-read-only-rootfs --require-no-new-privileges
```

上述检查将 `grep` 有输出显式转换为失败、无输出视为通过，并在同一 `DC` 渲染结果上验证 digest 与发布端口。本手册后续所有 Compose 命令必须在同一 Bash 会话复用 `DC`，不得省略 project/file/env 中的任一项。确认 secret 文件权限和服务隔离；只显示元数据，不输出内容：

```bash
stat -c '%a %U %G %n' "<TESTNET_SECRET_FILE>"
find "<SECRET_DIR>" -maxdepth 1 -type f -printf '%m %u %g %f\n'
quantctl secrets inspect-permissions --paths-from-env --redact
quantctl access-matrix verify \
  --rendered-compose "$RENDERED_COMPOSE" \
  --environment testnet \
  --allow-binance-secret-only testnet-probe-runner \
  --deny-whole-secret-directory-mount \
  --deny-production-secrets \
  --redact
quantctl network egress-verify --policy "$NETWORK_EGRESS_POLICY_FILE" --environment testnet --phase runtime --app-and-host --deny-unlisted
```

经人工确认当前 profile 为 `testnet` 后才启动基础服务：

```bash
read -r -p "输入 INIT-TESTNET-<RELEASE_ID> 继续: " CONFIRM
test "$CONFIRM" = "INIT-TESTNET-<RELEASE_ID>" || exit 1
"${DC[@]}" --profile testnet up -d --wait postgres redis monitoring
"${DC[@]}" --profile testnet ps
"${DC[@]}" --profile testnet run --rm --no-deps app-migrations alembic upgrade head
quantctl database migration-verify --expected-head "<EXPECTED_ALEMBIC_HEAD>" --read-write
"${DC[@]}" --profile testnet up -d --wait
```

`app-migrations` 是一次性任务，不是常驻容器；迁移命令退出非零或当前 head 不符时不得启动其余应用服务。

验证应用端口只绑定 loopback，并执行健康检查：

```bash
ss -H -lntp > "<LISTENER_EVIDENCE_FILE>"
quantctl network bind-verify \
  --listeners "<LISTENER_EVIDENCE_FILE>" \
  --require-loopback 8080,9090,9093 \
  --forbid-host-listen 5432,6379 \
  --deny-wildcard --deny-unapproved-public
quantctl access-matrix verify-runtime \
  --project aiq-testnet \
  --allow-binance-secret-only testnet-probe-runner \
  --deny-production-secrets --redact
curl --fail --silent --show-error http://127.0.0.1:8080/health/live
curl --fail --silent --show-error http://127.0.0.1:8080/health/ready
"${DC[@]}" --profile testnet ps
```

初始化远端归档只执行握手与临时测试对象，不上传 secret：

```bash
quantctl archive probe --profile testnet --remote "<ARCHIVE_REMOTE_NAME>"
quantctl archive crypto-verify --format age-v1 --recipient-file "$ARCHIVE_AGE_RECIPIENT_FILE" --require-x25519 --expected-recipient-sha256 "$ARCHIVE_AGE_RECIPIENT_SHA256"
quantctl archive receipt-roundtrip --remote "<ARCHIVE_REMOTE_NAME>" --require-remote-decrypt --verify-key "$ARCHIVE_RECEIPT_VERIFY_KEY_FILE" --delete-test-object
quantctl backup create --scope schema-and-config --profile testnet
quantctl backup verify --latest --restore-target "<ISOLATED_VERIFY_PATH>"
quantctl heartbeat probe --outbound-only --signed --algorithm Ed25519 --interval-seconds 30 --reject-replay --max-age-seconds 120 --missing-intervals 3
```

## 验收

- bootstrap bundle、OS/容器/工具版本和 hardening 摘要与签名 manifest 一致；镜像全部是不可变 digest。
- UTC 偏移 ≤50 ms，静态 IP 已记录，24 小时 RTT/丢包基线任务已启动。
- 公网仅暴露经批准的 SSH；应用、监控、PostgreSQL 和 Redis 不对公网监听。
- Testnet、Shadow、live 的网络、卷、数据库和订单前缀可证明隔离。
- 只有 `testnet-probe-runner` 能看到 Testnet secret；`execution-service` 和其他容器读取均被拒绝。
- 备份能恢复到隔离路径；归档测试对象经 age/X25519 加密、远端解密/Parquet 检查、双 SHA-256 和回执验签通过。
- 心跳每 30 秒仅出站 HTTPS；有效签名包可刷新 last-seen，连续 3 个间隔（约 90 秒）缺失触发告警，超过 120 秒、重放或伪造包被接收端拒绝。
- 启动后 canonical RuntimeState 为 `RISK_LOCKED`（未启动服务时为 `STOPPED`），不可能向生产端点下单。

## 停止与升级条件

发现生产 secret、意外公网端口、镜像摘要不符、时间偏移 >100 ms、磁盘异常、合规证据缺失或归档端不可验证时立即停止。保留容器和日志证据，不继续 [02 Testnet](02_TESTNET.md)。安全问题升级 P0/P1；基础设施问题升级 P2。修复后从 release 校验重新开始。

## 证据留存

保存命令输出（已脱敏）、bootstrap plan/approval/evidence、软件源与工具锁、用户/目录/SSH/防火墙/Docker/journald/chrony/sysctl/limits 摘要、完整 Compose 上下文指纹、OS/Compose 版本、端口和 egress 验证清单、时间状态、静态 IP、RTT 任务 ID、release manifest 摘要、镜像摘要、secret 权限/访问矩阵元数据、age recipient/回执验签公钥指纹、心跳防重放报告、备份恢复报告和审批签名。证据目录命名为 `<UTC_DATE>-initialize-<RELEASE_ID>`，生成 SHA-256 后同步远端审计库。
