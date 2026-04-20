## 小智 ESP32 Server Java - 部署指南

本文档描述如何将 xiaozhi-esp32-server-java 项目部署到 Linux 服务器（以阿里云 ECS 为例）。

---

### 架构概览

```
                    ┌─────────────┐
    用户浏览器 ────▶│  Nginx:8084 │──▶ 前端静态文件 (Vue3)
                    │             │──▶ /api/ 代理到 Server:8091
                    │             │──▶ /ws/xiaozhi/v1/ 代理到 Dialogue:8092
                    └─────────────┘
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
    ┌──────────────────┐   ┌──────────────────┐
    │ xiaozhi-server   │   │ xiaozhi-dialogue │
    │ (Spring Boot)    │   │ (Spring Boot)    │
    │ 端口: 8091       │   │ 端口: 8092       │
    │ 管理后台 API     │   │ WebSocket 对话   │
    └────────┬─────────┘   └────────┬─────────┘
             │                      │
             └──────────┬───────────┘
                        ▼
              ┌──────────────────┐
              │  MySQL + Redis   │
              └──────────────────┘
```

### 环境要求

| 组件 | 最低版本 | 说明 |
|------|---------|------|
| **操作系统** | CentOS 8 / Alibaba Cloud Linux 3 | x86_64 架构 |
| **内存** | 2GB+ | 1.8GB 勉强可用，需限制 JVM 堆内存 |
| **JDK** | 21 | 项目使用 Java 21 特性（虚拟线程等） |
| **Maven** | 3.6.3+ | 构建后端 JAR 包 |
| **Node.js** | 18+ | 构建前端（推荐在本地构建） |
| **Nginx** | 1.20+ | 托管前端 + 反向代理 |
| **MySQL** | 8.0+ | 数据库 |
| **Redis** | 6.0+ | 缓存 + 分布式锁 |

---

### 第一步：安装基础环境

#### 1.1 安装 JDK 21

```bash
# CentOS / Alibaba Cloud Linux
yum install -y java-21-openjdk java-21-openjdk-devel

# 验证（注意：系统默认 java 可能指向其他版本，需用全路径）
/usr/lib/jvm/java-21-openjdk/bin/java -version
```

> **注意**：如果系统安装了多个 JDK 版本，`java` 命令可能指向旧版本。后续启动命令需使用 JDK 21 的**完整路径** `/usr/lib/jvm/java-21-openjdk/bin/java`。

#### 1.2 安装 Maven 3.9+

系统 yum 源的 Maven 版本通常较低（3.6.2），不满足项目要求（≥3.6.3）。建议手动安装：

```bash
cd /opt
curl -L "https://repo.huaweicloud.com/apache/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.tar.gz" -o maven.tar.gz
tar xzf maven.tar.gz && rm maven.tar.gz

# 验证
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk
/opt/apache-maven-3.9.9/bin/mvn -v
```

#### 1.3 安装 Node.js（可选）

如果服务器内存充足（≥4GB），可以在服务器上构建前端：

```bash
curl -fsSL https://rpm.nodesource.com/setup_22.x | bash -
yum install -y nodejs
node -v && npm -v
```

> **低内存服务器（<4GB）**：`vite build` 会 OOM，建议在本地构建后上传 `dist` 目录。

#### 1.4 安装 Nginx

```bash
yum install -y nginx
systemctl enable nginx
```

#### 1.5 安装 MySQL 和 Redis

如果服务器上还没有 MySQL 和 Redis，可以通过 yum 安装或使用 Docker：

```bash
# MySQL
yum install -y mysql-server
systemctl start mysqld && systemctl enable mysqld

# Redis
yum install -y redis
systemctl start redis && systemctl enable redis
```

初始化数据库：

```bash
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS xiaozhi DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

> 项目使用 Flyway 自动管理数据库迁移，首次启动 `xiaozhi-server` 时会自动创建表结构。

#### 1.6 配置 Swap（低内存服务器必须）

对于 ≤2GB 内存的服务器，**必须开启 Swap**，否则两个 Java 进程 + MySQL + Redis 极易触发 OOM Killer：

```bash
# 创建 1GB swap 文件
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile

# 开机自动挂载
echo "/swapfile swap swap defaults 0 0" >> /etc/fstab

# 验证
free -m
```

#### 1.7 优化 MySQL 和 Redis 内存（低内存服务器推荐）

本项目对 MySQL 和 Redis 的性能要求不高，低内存服务器应最小化其内存占用。

**MySQL 内存优化** - 将以下配置追加到 `/etc/my.cnf` 的 `[mysqld]` 段中：

> **注意**：MySQL 8.0 的配置文件搜索路径为 `/etc/my.cnf`、`/etc/mysql/my.cnf`，**不包含** `/etc/my.cnf.d/`。请确认配置写入了正确的文件。

```ini
[mysqld]
# === 原有配置 ===
bind-address = 0.0.0.0
port=3307

# === 极限内存优化（低内存服务器，不在意性能） ===
# buffer_pool 最小 128MB（MySQL 8.0 的 chunk_size 硬限制，无法再降）
innodb_buffer_pool_size=128M
innodb_buffer_pool_instances=1
key_buffer_size=256K
max_connections=10
table_open_cache=16
table_definition_cache=32
thread_cache_size=1
innodb_log_buffer_size=1M
tmp_table_size=1M
max_heap_table_size=1M
sort_buffer_size=64K
read_buffer_size=64K
join_buffer_size=64K
read_rnd_buffer_size=64K
bulk_insert_buffer_size=1M
innodb_sort_buffer_size=256K
# 关闭性能监控（节省约 100MB）
performance_schema=OFF
# 关闭 binlog（单机不需要主从复制）
skip-log-bin
# 最小 redo log
innodb_redo_log_capacity=8M
# 全文索引缓存降到最小
innodb_ft_cache_size=1600000
innodb_ft_total_cache_size=32000000
innodb_ft_result_cache_limit=2000000
# 网络缓冲
net_buffer_length=8192
max_allowed_packet=4M
# 减少打开文件数
host_cache_size=0
open_files_limit=256
innodb_open_files=64
```

```bash
# 重启 MySQL 使配置生效
systemctl restart mysqld

# 验证关键参数
mysql -u root -p -e "SHOW VARIABLES LIKE 'performance_schema'; SHOW VARIABLES LIKE 'max_connections';"
```

**Redis 内存优化**：

```bash
# 限制 Redis 最大内存为 8MB（本项目仅用于缓存和分布式锁，8MB 足够）
redis-cli -a <Redis密码> CONFIG SET maxmemory 8mb
redis-cli -a <Redis密码> CONFIG SET maxmemory-policy allkeys-lru
redis-cli -a <Redis密码> CONFIG SET hz 2
redis-cli -a <Redis密码> CONFIG SET activedefrag no
redis-cli -a <Redis密码> CONFIG REWRITE
```

**关闭不需要的系统服务**（节省约 100MB）：

> **重要**：使用 `systemctl mask` 而非 `disable`，确保服务不会被其他服务依赖拉起，重启后也不会恢复。

```bash
# Docker（无容器运行时，节省约 50MB）
for svc in docker docker.socket containerd; do
  systemctl stop $svc && systemctl mask $svc
done

# pipewire 音频服务（服务器不需要，节省约 8MB）
XDG_RUNTIME_DIR=/run/user/0 systemctl --user stop pipewire.socket pipewire pipewire-pulse wireplumber
XDG_RUNTIME_DIR=/run/user/0 systemctl --user mask pipewire.socket pipewire pipewire-pulse wireplumber
systemctl --global mask pipewire.socket pipewire pipewire-pulse wireplumber

# 不需要的系统服务（节省约 30MB）
for svc in gssproxy rtkit-daemon tuned polkit rngd atd rsyslog serial-getty@ttyS0; do
  systemctl stop $svc 2>/dev/null; systemctl mask $svc
done

# cloud-init 系列（首次启动后不再需要）
for svc in cloud-init cloud-init-local cloud-config cloud-final; do
  systemctl stop $svc 2>/dev/null; systemctl mask $svc
done

# 其他不需要的服务
for svc in kdump ecs_mq NetworkManager-wait-online NetworkManager-dispatcher timedatex update-motd loadmodules selinux-autorelabel-mark; do
  systemctl stop $svc 2>/dev/null; systemctl mask $svc
done

# 限制 journald 日志大小
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/size-limit.conf << EOF
[Journal]
SystemMaxUse=16M
RuntimeMaxUse=8M
EOF
systemctl restart systemd-journald
```

> **注意**：阿里云安骑士（aegis，~23MB）和 aliyun-assist（~11MB）是云管理组件，关闭后会影响云控制台的安全检测和远程命令功能，建议保留。

**内核参数优化**（减少内核保留内存，更积极使用 Swap）：

```bash
cat > /etc/sysctl.d/99-memory-optimize.conf << EOF
# 更积极使用 swap，减少 OOM 风险（默认 0，改为 60）
vm.swappiness=60
# 更积极回收 dentry/inode 缓存（默认 100，改为 200）
vm.vfs_cache_pressure=200
# 降低内核保留内存（默认 45MB，改为 16MB）
vm.min_free_kbytes=16384
EOF
sysctl --system
```

> **优化后最终保留的 enabled 服务**（仅 15 个）：
> `aegis` `aliyun` `chronyd` `crond` `dbus` `getty` `mysqld` `NetworkManager` `nginx` `redis` `sshd` `sysfs` `systemd-pstore` `xiaozhi-server` `xiaozhi-dialogue`

> **内存预算参考**（2GB 服务器，极限优化后实测）：
> | 组件 | RSS 内存 |
> |------|---------|
> | xiaozhi-dialogue | ~615MB |
> | xiaozhi-server | ~488MB |
> | MySQL | ~143MB（buffer_pool 128MB 是 MySQL 8.0 硬限制） |
> | 阿里云组件（aegis + assist） | ~35MB |
> | journald | ~23MB |
> | Redis | ~8MB |
> | 系统 + Nginx + 其他 | ~24MB |
> | **合计** | **~1336MB** 已用 / 534MB 可用 / 1GB Swap 缓冲 |

#### 1.8 进一步降低 MySQL 内存：迁移到 MariaDB（可选）

MySQL 8.0 的 `innodb_buffer_pool_size` 最小 128MB 是硬限制（`innodb_buffer_pool_chunk_size` 最小值 128MB，源码硬编码），无法通过配置突破。如果需要进一步压缩数据库内存，可以迁移到 **MariaDB**。

**为什么选 MariaDB？**
- MariaDB 是 MySQL 的开源分支，由 MySQL 创始人维护，**完全兼容 MySQL 协议和 SQL 语法**
- `innodb_buffer_pool_size` 最小可设到 **5MB**（没有 chunk_size 128MB 的硬限制）
- Spring Boot / MyBatis / JDBC 驱动完全兼容，**Java 代码零修改**
- 冷启动内存约 30-40MB（vs MySQL 8.0 约 145MB），**可节省约 100MB**

**其他替代方案对比**：

| 方案 | buffer pool 最小值 | 兼容性 | 推荐度 |
|------|-------------------|--------|--------|
| **MariaDB 10.x/11.x** | **5MB** | 完全兼容 MySQL，代码零修改 | ⭐⭐⭐⭐ **推荐** |
| MySQL 5.7 | 5MB | 兼容但已 EOL，无安全更新 | ⭐⭐ 不推荐 |
| PostgreSQL | 128KB（shared_buffers） | SQL 语法有差异，需改代码 | ⭐⭐ 可选 |
| SQLite | 几乎不占内存 | 不支持并发写，需大改 ORM 层 | ⭐ 不推荐 |

**迁移步骤**（MySQL 8.0 → MariaDB）：

```bash
# 1. 备份数据
mysqldump -u root -p --all-databases --routines --triggers > /opt/xiaozhi/backup.sql

# 2. 停止并卸载 MySQL
systemctl stop mysqld
yum remove mysql-server mysql-community-server

# 3. 安装 MariaDB
yum install mariadb-server

# 4. 配置 MariaDB（/etc/my.cnf）
# 将 innodb_buffer_pool_size 从 128M 改为 5M
# 其他参数保持不变

# 5. 启动并导入数据
systemctl start mariadb
mysql -u root -p < /opt/xiaozhi/backup.sql

# 6. 更新 systemd 服务依赖（将 mysqld.service 改为 mariadb.service）
# 7. 验证应用连接正常
```

> **注意**：迁移前务必做好数据备份。MariaDB 与 MySQL 8.0 在少数高级特性上有差异（如 JSON 函数、窗口函数语法），但本项目使用的基础 SQL 完全兼容。

---

### 第二步：上传代码

将项目代码上传到服务器的 `/opt/xiaozhi` 目录：

```bash
# 方式一：Git 克隆
cd /opt
git clone <仓库地址> xiaozhi

# 方式二：本地打包上传
# 本地执行
tar czf xiaozhi.tar.gz --exclude=node_modules --exclude=.git --exclude=target \
    -C /path/to/xiaozhi-esp32-server-java .
scp xiaozhi.tar.gz root@<服务器IP>:/opt/

# 服务器执行
cd /opt && mkdir -p xiaozhi && tar xzf xiaozhi.tar.gz -C xiaozhi && rm xiaozhi.tar.gz
```

---

### 第三步：构建后端

```bash
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk
export PATH=/opt/apache-maven-3.9.9/bin:$PATH

cd /opt/xiaozhi
mvn clean package -DskipTests
```

构建成功后会生成：
- `xiaozhi-server/target/xiaozhi-server-<version>.jar`
- `xiaozhi-dialogue/target/xiaozhi-dialogue-<version>-exec.jar`

---

### 第四步：构建前端

#### 方式 A：在服务器上构建（需 ≥4GB 内存）

```bash
cd /opt/xiaozhi/web
npm install
npx vite build
```

#### 方式 B：在本地构建后上传（推荐低内存服务器）

```bash
# === 本地执行 ===

# 1. 修改 web/.env.production 中的地址
# VITE_WS_URL=ws://<服务器IP>:8092/ws/xiaozhi/v1
# VITE_BACKEND_URL=http://<服务器IP>:8091

# 2. 构建
cd web && npm install && npx vite build

# 3. 上传
scp -r dist root@<服务器IP>:/opt/xiaozhi/web/
```

---

### 第五步：上传模型文件

Dialogue 服务需要 VAD（语音活动检测）模型文件：

```bash
# 确保模型目录存在
mkdir -p /opt/xiaozhi/models

# 方式一：使用项目自带的下载脚本
cd /opt/xiaozhi && bash scripts/download_models.sh vad

# 方式二：从本地上传（如果下载太慢）
scp models/silero_vad.onnx root@<服务器IP>:/opt/xiaozhi/models/
```

> 如果需要 Vosk 离线语音识别，还需下载 Vosk 模型：`bash scripts/download_models.sh stt`

---

### 第六步：配置

#### 6.1 创建生产环境配置

**Server 配置** - 创建 `xiaozhi-server/src/main/resources/application-prod.yml`：

```yaml
spring:
  datasource:
    driver-class-name: com.mysql.cj.jdbc.Driver
    url: jdbc:mysql://localhost:<MySQL端口>/xiaozhi?useUnicode=true&characterEncoding=utf8&serverTimezone=GMT%2B8&useSSL=false&allowPublicKeyRetrieval=true&rewriteBatchedStatements=true
    username: root
    password: <MySQL密码>
  data:
    redis:
      host: localhost
      port: 6379
      password: <Redis密码>

xiaozhi:
  server:
    domain: <你的域名>
```

> **关于 `domain` 配置**：
> - 如果你有域名（如 `example.com`），填入域名，系统会生成 `wss://ws.example.com/ws/xiaozhi/v1/` 等地址
> - 如果**没有域名，只有 IP**，请留空或不配置此项，系统会自动检测服务器公网 IP 并生成 `ws://<IP>:8092/ws/xiaozhi/v1/` 等地址
> - 如果 JAR 包内已打包了错误的 domain 值，可通过外部配置文件覆盖（见下方 6.3）

#### 6.3 覆盖 JAR 包内的 domain 配置（无域名时必须）

如果 JAR 包内的 `application-prod.yml` 已经打包了一个域名（如 `connectai.chat`），但你的服务器没有配置该域名解析，需要创建外部覆盖配置文件：

```bash
cat > /opt/xiaozhi/server-override.yml << 'EOF'
xiaozhi:
  server:
    domain: ""
EOF
```

> 启动 server 时需通过 `-Dspring.config.additional-location` 加载此文件（见第七步）。

**Dialogue 配置** - 创建 `/opt/xiaozhi/dialogue-prod.yml`：

```yaml
spring:
  datasource:
    driver-class-name: com.mysql.cj.jdbc.Driver
    url: jdbc:mysql://localhost:<MySQL端口>/xiaozhi?useUnicode=true&characterEncoding=utf8&serverTimezone=GMT%2B8&useSSL=false&allowPublicKeyRetrieval=true&rewriteBatchedStatements=true
    username: root
    password: <MySQL密码>
  data:
    redis:
      host: localhost
      port: 6379
      password: <Redis密码>
```

> 同时检查 `xiaozhi-server/src/main/resources/redisson-config.yml` 中的 Redis 密码是否正确。

#### 6.2 配置 Nginx

创建 `/etc/nginx/conf.d/xiaozhi.conf`：

```nginx
server {
    listen 8084;
    server_name _;

    root /opt/xiaozhi/web/dist;
    index index.html;

    # 前端路由 - SPA
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API 代理到 server
    location /api/ {
        proxy_pass http://127.0.0.1:8091/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket 代理到 dialogue
    location /ws/xiaozhi/v1/ {
        proxy_pass http://127.0.0.1:8092/ws/xiaozhi/v1/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

验证并启动 Nginx：

```bash
nginx -t && systemctl start nginx
```

---

### 第七步：配置 systemd 服务（推荐）

使用 systemd 管理 Java 服务，实现**开机自启**和**崩溃自动重启**（OOM 被杀后 10 秒内自动恢复）。

#### 7.1 创建 systemd 服务

```bash
# 创建日志目录
mkdir -p /opt/xiaozhi/logs
```

**xiaozhi-server 服务** - 创建 `/etc/systemd/system/xiaozhi-server.service`：

```ini
[Unit]
Description=Xiaozhi Server (Spring Boot)
After=network.target mysqld.service redis.service
Wants=mysqld.service redis.service
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=root
WorkingDirectory=/opt/xiaozhi
ExecStart=/usr/lib/jvm/java-21-openjdk/bin/java -Xms64m -Xmx256m -XX:MaxMetaspaceSize=128m -XX:ReservedCodeCacheSize=64m -Dspring.profiles.active=prod -Dspring.config.additional-location=file:/opt/xiaozhi/server-override.yml -jar /opt/xiaozhi/xiaozhi-server/target/xiaozhi-server-5.0.0.jar
StandardOutput=append:/opt/xiaozhi/logs/server.log
StandardError=append:/opt/xiaozhi/logs/server.log
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**xiaozhi-dialogue 服务** - 创建 `/etc/systemd/system/xiaozhi-dialogue.service`：

```ini
[Unit]
Description=Xiaozhi Dialogue (Spring Boot WebSocket)
After=network.target mysqld.service redis.service
Wants=mysqld.service redis.service
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=root
WorkingDirectory=/opt/xiaozhi
ExecStart=/usr/lib/jvm/java-21-openjdk/bin/java -Xms128m -Xmx384m -XX:MaxMetaspaceSize=128m -XX:ReservedCodeCacheSize=64m -Dspring.profiles.active=prod -Dspring.config.additional-location=file:/opt/xiaozhi/dialogue-prod.yml -Dxiaozhi.vad.model.path=/opt/xiaozhi/models/silero_vad.onnx -jar /opt/xiaozhi/xiaozhi-dialogue/target/xiaozhi-dialogue-5.0.0-exec.jar
StandardOutput=append:/opt/xiaozhi/logs/dialogue.log
StandardError=append:/opt/xiaozhi/logs/dialogue.log
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

> **关键参数说明**：
> - **`Restart=always`**：进程退出后（包括被 OOM Killer 杀掉）自动重启
> - **`RestartSec=10`**：重启间隔 10 秒，避免频繁重启
> - **`StartLimitIntervalSec=300` / `StartLimitBurst=5`**：5 分钟内最多重启 5 次，防止无限重启
> - 如果不需要覆盖 domain（已有正确域名），可去掉 server 的 `-Dspring.config.additional-location=...` 参数

#### 7.2 启动服务

```bash
# 加载配置并设为开机自启
systemctl daemon-reload
systemctl enable xiaozhi-server xiaozhi-dialogue

# 启动
systemctl start xiaozhi-server xiaozhi-dialogue
```

> **JVM 内存参数参考**：
> | 服务器内存 | Server `-Xmx` | Dialogue `-Xmx` |
> |-----------|---------------|-----------------|
> | ≤2GB | 256m | 384m |
> | 4GB | 512m | 768m |
> | ≥8GB | 1g | 2g |

#### 7.2 验证启动

```bash
# 检查进程
ps aux | grep java | grep -v grep

# 检查端口
ss -tlnp | grep -E "8091|8092"

# 检查健康状态
curl http://localhost:8091/actuator/health
curl http://localhost:8092/actuator/health

# 查看日志
tail -f /opt/xiaozhi/logs/server.log
tail -f /opt/xiaozhi/logs/dialogue.log
```

---

### 第八步：开放端口

#### 8.1 服务器防火墙（如果启用了 firewalld）

```bash
firewall-cmd --permanent --add-port=8084/tcp
firewall-cmd --permanent --add-port=8091/tcp
firewall-cmd --permanent --add-port=8092/tcp
firewall-cmd --reload
```

#### 8.2 阿里云安全组

登录 [阿里云 ECS 控制台](https://ecs.console.aliyun.com/)，找到实例 → **安全组** → **配置规则** → **入方向**，添加：

| 端口范围 | 协议 | 授权对象 | 说明 |
|---------|------|---------|------|
| 8084 | TCP | 0.0.0.0/0 | 前端 Web 页面 |
| 8091 | TCP | 0.0.0.0/0 | Server API |
| 8092 | TCP | 0.0.0.0/0 | Dialogue WebSocket |

---

### 日常运维

#### 查看服务状态

```bash
systemctl status xiaozhi-server xiaozhi-dialogue
```

#### 停止服务

```bash
systemctl stop xiaozhi-server xiaozhi-dialogue
```

#### 重启服务

```bash
systemctl restart xiaozhi-server xiaozhi-dialogue
```

#### 更新代码后重新部署

```bash
# 1. 上传新代码到 /opt/xiaozhi

# 2. 重新构建后端
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk
export PATH=/opt/apache-maven-3.9.9/bin:$PATH
cd /opt/xiaozhi && mvn clean package -DskipTests

# 3. 重新构建前端（本地构建后上传 dist）

# 4. 重启服务
systemctl restart xiaozhi-server xiaozhi-dialogue
```

#### 查看日志

```bash
# 实时查看
tail -f /opt/xiaozhi/logs/server.log
tail -f /opt/xiaozhi/logs/dialogue.log

# 搜索错误
grep -i "error\|exception" /opt/xiaozhi/logs/server.log | tail -20
```

---

### 常见问题

#### Q: Java 版本不对，报 `UnsupportedClassVersionError`

系统默认 `java` 指向低版本 JDK。解决方案：使用 JDK 21 的完整路径 `/usr/lib/jvm/java-21-openjdk/bin/java`。

#### Q: Maven 构建报 `requires Maven version 3.6.3`

系统 yum 安装的 Maven 版本太低。手动安装 Maven 3.9+（见第一步）。

#### Q: 前端 `vite build` 被 kill（exit code 137）

服务器内存不足，OOM Killer 杀掉了进程。解决方案：在本地构建前端后上传 `dist` 目录。

#### Q: Dialogue 启动报 `silero_vad.onnx File doesn't exist`

模型文件缺失或路径不对。ONNX Runtime 加载模型时不一定使用 JVM 的工作目录，因此**必须使用绝对路径**。解决方案：

1. 确保模型文件存在于 `/opt/xiaozhi/models/silero_vad.onnx`
2. 启动时通过 `-D` 参数指定绝对路径：`-Dxiaozhi.vad.model.path=/opt/xiaozhi/models/silero_vad.onnx`

#### Q: WebSocket 连接失败（code=1006）

检查 WebSocket 路径是否正确。Dialogue 服务的 WebSocket 端点路径为 `/ws/xiaozhi/v1/`（注意有 `/ws/` 前缀），而非 `/xiaozhi/v1/`。确保：
1. 前端 `.env.production` 中 `VITE_WS_URL` 包含 `/ws/` 前缀
2. Nginx 代理配置中 location 路径为 `/ws/xiaozhi/v1/`

#### Q: Docker Hub 镜像拉取超时

国内无法直接访问 Docker Hub。如果需要 Docker 部署，配置镜像加速器：

```bash
cat > /etc/docker/daemon.json << 'EOF'
{
  "registry-mirrors": [
    "https://registry.cn-hangzhou.aliyuncs.com",
    "https://mirror.ccs.tencentyun.com"
  ]
}
EOF
systemctl restart docker
```

#### Q: Server 进程被 OOM Killer 杀掉

低内存服务器（≤2GB）同时运行两个 Java 进程 + MySQL + Redis 容易触发 OOM。排查和解决：

```bash
# 查看是否被 OOM Killer 杀掉
dmesg | grep -i "oom\|killed" | tail -5

# 查看当前内存
free -m
```

解决方案：
1. **开启 Swap**（见 1.6）
2. **降低 JVM 堆内存**：Server 用 `-Xmx256m`，Dialogue 用 `-Xmx384m`
3. **优化 MySQL/Redis 内存**（见 1.7）
4. 加上 `-XX:MaxMetaspaceSize=128m -XX:ReservedCodeCacheSize=64m` 限制堆外内存

#### Q: OTA 返回的 WebSocket 地址指向错误的域名

JAR 包内的 `application-prod.yml` 可能打包了一个域名（如 `connectai.chat`），但服务器没有该域名解析。解决方案：

1. 创建 `/opt/xiaozhi/server-override.yml`，将 `domain` 设为空字符串
2. 启动时加上 `-Dspring.config.additional-location=file:/opt/xiaozhi/server-override.yml`

详见第六步 6.3 节。

#### Q: ESP32 连接 OTA 报 `code=104`（ECONNRESET）

通常是 Server 服务（8091 端口）没有运行。检查：
1. `ps aux | grep xiaozhi-server` 确认进程存在
2. `ss -tlnp | grep 8091` 确认端口监听
3. 如果进程不在，检查是否被 OOM Killer 杀掉（见上方）

#### Q: 外部无法访问服务

检查：
1. 服务是否启动：`ss -tlnp | grep -E "8084|8091|8092"`
2. 服务器防火墙：`firewall-cmd --state`
3. 云服务商安全组规则是否开放了对应端口
