# OpenQ Windows + WSL2 完整部署文档

本文面向一台运行 `Windows 10/11` 的开发或演示设备，目标部署拓扑固定为：

- `Windows` 主机负责运行 `Docker Desktop`
- `Docker Desktop` 负责承载 `FISCO BCOS` 四节点私有链容器
- `WSL2 Ubuntu` 负责运行 `OpenClaw`、`FastAPI`、网页演示端、虚拟 App、审计与状态文件
- 浏览器从 `Windows` 侧访问 `http://localhost:8000`

这也是当前仓库最接近实际交付形态、最稳定、最容易复现的一种部署方式。

## 1. 部署目标与最终结构

本项目在这套部署中分为三层：

| 组件 | 部署位置 | 作用 |
| --- | --- | --- |
| `Docker Desktop` | Windows 主机 | 提供容器运行时 |
| `FISCO BCOS` | Docker Desktop 容器 | 提供私有链、权限校验、链上摘要 |
| `OpenClaw gateway` | WSL2 Ubuntu | 提供真实 OpenClaw WebSocket 接入 |
| `FastAPI + 网页演示端` | WSL2 Ubuntu | 提供网页前端、API、网关、虚拟 App |
| `data/`、`logs/`、`runtime-deps/` | WSL2 Ubuntu 项目目录 | 保存状态、审计、链运行资产与日志 |

默认访问入口如下：

| 功能 | 默认地址 |
| --- | --- |
| 网页演示端 | `http://localhost:8000` |
| OpenClaw WebSocket | `ws://localhost:18789` |
| OpenClaw 状态接口 | `http://localhost:8000/api/openclaw/status` |
| 链状态接口 | `http://localhost:8000/api/chain/status` |
| FISCO 端口 | `20200-20203`、`30300-30303` |

## 2. 部署前提

### 2.1 机器建议

建议至少满足下面条件：

- `Windows 10/11`
- `16 GB` 以上内存
- `4` 核以上 CPU
- `30 GB` 以上空闲磁盘

如果机器资源偏紧，项目仍可能运行，但 `Docker Desktop`、`OpenClaw` 和本地护栏模型首轮初始化会明显变慢。

### 2.2 目录规划

仓库必须放在 `WSL2` 的 Linux 文件系统中，例如：

```bash
~/project/openq
```

不建议放在这些位置：

- `/mnt/c/...`
- OneDrive 同步目录
- Windows 桌面映射目录

原因很简单：跨文件系统路径在权限、性能、软链接和脚本行为上都更容易出问题。

### 2.3 本文默认约定

后续命令按下面约定描述：

- `Windows PowerShell` 命令在 `Windows` 终端执行
- `Ubuntu` 命令在 `WSL2 Ubuntu` 终端执行
- 项目根目录默认为 `~/project/openq`

## 3. 第一次安装环境

### 3.1 在 Windows 上启用 WSL2

以管理员身份打开 `PowerShell`，执行：

```powershell
wsl --install
wsl --set-default-version 2
```

执行完成后重启 Windows。

如果机器上已经安装过 `WSL`，可以继续确认当前发行版是否已是 `WSL2`：

```powershell
wsl -l -v
```

### 3.2 安装 Ubuntu

可从 Microsoft Store 安装 `Ubuntu`，也可以直接执行：

```powershell
wsl --install -d Ubuntu
```

首次启动 Ubuntu 时，系统会要求创建 Linux 用户名和密码。

### 3.3 在 Windows 上安装 Docker Desktop

在 Windows 主机安装 `Docker Desktop`，安装后至少确认这些设置：

1. 启用 `Use the WSL 2 based engine`
2. 在 `Settings -> Resources -> WSL Integration` 中勾选你的 Ubuntu 发行版
3. 给 Docker 分配足够内存，建议不少于 `6 GB`
4. 启动后确认 Docker Desktop 状态为 Running

安装完成后，在 `Windows` 侧无需手工部署链容器，后续由项目脚本在 `WSL2` 内通过 Docker CLI 驱动 Docker Desktop。

### 3.4 在 WSL2 Ubuntu 中安装基础工具

进入 Ubuntu，执行：

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git curl unzip ca-certificates build-essential lsof
```

如果后续需要排查端口、进程和网络，`lsof` 会比较有用。

### 3.5 在 WSL2 Ubuntu 中安装 uv

项目当前使用 `uv` 作为 Python 环境和运行入口。执行：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv --version
```

建议把 `~/.local/bin` 放入 shell 的 `PATH`。如果安装脚本没有自动处理，可把下面内容加入 `~/.bashrc`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

然后执行：

```bash
source ~/.bashrc
```

### 3.6 在 WSL2 Ubuntu 中准备 Python 3.12

执行：

```bash
uv python install 3.12
```

这个项目的 Python 入口和依赖安装都按 `Python 3.12` 组织。

### 3.7 在 WSL2 Ubuntu 中安装 OpenClaw

OpenClaw 不打包在仓库内，需要单独安装。优先参考官方文档：

- https://docs.openclaw.ai/install
- https://docs.openclaw.ai/start/setup

仓库中还缓存了一份 npm 包归档，可用于减少联网拉包次数：

- `runtime-deps/packages/openclaw-2026.4.5.tgz`

安装完成后，先在 Ubuntu 终端确认：

```bash
openclaw --version
openclaw health
```

当前项目默认连接地址是：

```text
ws://localhost:18789
```

如果你把 OpenClaw 配到了别的端口或地址，不需要改源码，直接设置环境变量：

```bash
export OPENCLAW_URL="ws://127.0.0.1:18789"
```

## 4. 获取项目与安装依赖

### 4.1 拉取项目代码

在 Ubuntu 里执行：

```bash
mkdir -p ~/project
cd ~/project
git clone <你的仓库地址> openq
cd openq
```

如果项目已经存在，只要确认最终路径位于 `WSL2` 本地 Linux 文件系统即可。

### 4.2 安装 Python 依赖

在项目根目录执行：

```bash
uv sync
```

这一步会创建虚拟环境并安装 `pyproject.toml` 与 `uv.lock` 中定义的依赖。

安装完成后先确认基础入口：

```bash
uv run python -V
uv run python -m unittest discover -s tests -v
```

如果单元测试跑通，说明主工程依赖基本完整。

## 5. 护栏模型与环境变量

仓库已经内置了护栏模型权重，路径分别是：

- `runtime-deps/models/BAAI/bge-base-zh-v1.5`
- `runtime-deps/models/BAAI/bge-reranker-v2-m3`

因此正常部署时不需要再额外下载这两套权重，系统会优先使用仓库内本地文件。

项目默认会读取这几个环境变量：

| 变量 | 推荐值 | 说明 |
| --- | --- | --- |
| `OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD` | `0` | 是否允许回退到远程补充下载；默认不需要 |
| `OPENQ_GUARD_DEVICE` | `cpu` | 护栏运行设备 |
| `OPENQ_GUARD_RUNTIME` | `formal` | 护栏运行模式 |
| `OPENQ_HOST` | `0.0.0.0` | FastAPI 监听地址 |
| `OPENQ_PORT` | `8000` | FastAPI 监听端口 |
| `OPENQ_URL_HOST` | `127.0.0.1` | 脚本健康检查所用访问地址 |
| `OPENCLAW_PORT` | `18789` | OpenClaw gateway 端口 |
| `OPENCLAW_URL` | `ws://127.0.0.1:18789` | OpenQ 连接 OpenClaw 的完整地址 |
| `OPENQ_MODEL_ROOT` | `$(pwd)/runtime-deps/models` | 本地护栏模型根目录 |

推荐在 `~/.bashrc` 或当前部署会话中先设置：

```bash
export OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD=0
export OPENQ_GUARD_DEVICE=cpu
export OPENQ_GUARD_RUNTIME=formal
export OPENQ_MODEL_ROOT="$PWD/runtime-deps/models"
```

保持 `0` 时会直接使用仓库内本地权重，不会再去联网下载模型。

## 6. 启动链服务

### 6.1 确认 Docker Desktop 已启动

在 Windows 侧先打开 `Docker Desktop`，确保状态正常。

然后回到 Ubuntu，执行：

```bash
docker info
```

如果这条命令失败，优先检查：

- Docker Desktop 是否真的已经启动
- Docker Desktop 的 WSL Integration 是否勾选了当前 Ubuntu
- 当前终端是否需要重开一次

### 6.2 启动 FISCO BCOS

在项目根目录执行：

```bash
./scripts/fisco-up.sh
```

脚本会自动完成这些动作：

1. 检查 Docker 是否可用
2. 创建 `openq-fisco` Docker 网络
3. 检查本地是否已有 `fiscoorg/fiscobcos:v3.6.0`
4. 若镜像不存在，则从仓库中的镜像归档导入
5. 使用 `runtime-deps/fisco-portable` 里的节点配置启动四节点链

### 6.3 检查链状态

执行：

```bash
./scripts/fisco-status.sh
```

正常情况下，你应该看到：

- 4 个 FISCO 容器处于 `Up`
- SDK 证书目录输出为 `runtime-deps/fisco-portable/nodes/127.0.0.1/sdk`
- `20200-20203` 与 `30300-30303` 端口已监听

### 6.4 使用和停止链

进入控制台：

```bash
./scripts/fisco-console.sh
```

停止链：

```bash
./scripts/fisco-down.sh
```

## 7. 检查 OpenClaw

在 Ubuntu 终端执行：

```bash
./scripts/openclaw-check.sh
```

这个脚本会检查两件事：

1. `openclaw` 命令是否存在
2. `openclaw health` 是否可通过

如果 `openclaw health` 失败，不要继续启动 OpenQ 主服务，先把 OpenClaw 自身状态修好。

## 8. 启动 OpenQ 主服务

你有两种启动方式。

### 8.1 推荐方式：一键启动整套环境

在项目根目录执行：

```bash
./scripts/openq-up.sh
```

这个脚本会按固定顺序启动：

1. 检查或启动 FISCO BCOS
2. 检查或启动 OpenClaw gateway
3. 启动 `FastAPI` 演示端
4. 输出演示页地址、OpenClaw 状态地址、链状态地址

这个脚本采用后台启动模式。脚本退出后，服务仍会继续运行。

### 8.2 手工方式：单独启动 FastAPI

如果你需要前台调试 `uvicorn`，可使用：

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

这种方式适合开发调试，不适合正式演示时一键拉起整套环境。

## 9. 从 Windows 侧访问网页前端

项目的网页前端不是部署在 Windows 主机上，而是运行在 `WSL2 Ubuntu` 里的 `FastAPI` 服务中。

只要 `uvicorn` 绑定的是 `0.0.0.0:8000`，Windows 浏览器通常可以直接访问：

```text
http://localhost:8000
```

因为 WSL2 默认会把 Linux 侧的监听端口映射到 Windows 的 `localhost`。

如果 `localhost:8000` 无法访问，可以在 Ubuntu 中执行：

```bash
hostname -I
```

假设输出为 `172.24.116.85`，那也可以尝试从 Windows 浏览器访问：

```text
http://172.24.116.85:8000
```

但在正常情况下，优先还是使用 `http://localhost:8000`。

## 10. 部署完成后的验证顺序

建议按下面顺序验证：

### 10.1 预检

```bash
./scripts/preflight.sh
```

这个脚本会检查：

- Python 与护栏模型状态
- FISCO 状态
- OpenClaw 状态
- FastAPI 应用是否可装载

### 10.2 验证网页是否可访问

在 Windows 浏览器访问：

```text
http://localhost:8000
```

如果页面能打开，说明前端网页已经正确运行在 WSL2 中。

### 10.3 验证两个核心状态接口

在 Ubuntu 中执行：

```bash
curl -fsS http://127.0.0.1:8000/api/chain/status
curl -fsS http://127.0.0.1:8000/api/openclaw/status
```

正常情况下，应该看到：

- `/api/chain/status` 返回 `available: true`
- `/api/openclaw/status` 返回 `available: true`

### 10.4 运行项目验收脚本

```bash
./scripts/acceptance.sh
```

这个脚本会跑一轮项目级验收，适合在部署完成后做最终确认。

## 11. 日常启动与停止

### 11.1 每次开机后的推荐顺序

1. 在 Windows 启动 `Docker Desktop`
2. 打开 `WSL2 Ubuntu`
3. 进入项目目录 `cd ~/project/openq`
4. 执行 `./scripts/openq-up.sh`
5. 在 Windows 浏览器打开 `http://localhost:8000`

### 11.2 关闭整套服务

```bash
./scripts/openq-down.sh
```

这个脚本会依次停止：

- FastAPI 演示端
- OpenClaw gateway
- FISCO BCOS

## 12. 日志与常用排查位置

运行日志统一写在：

- `logs/openq-up.log`
- `logs/fisco-up.log`
- `logs/openq-app.log`
- `logs/openclaw.log`
- `logs/openq-down.log`

如果启动失败，优先看对应日志文件，不要先改代码。

## 13. 常见故障与处理办法

### 13.1 `docker info` 失败

常见原因：

- Docker Desktop 未启动
- Docker Desktop 没有启用当前 Ubuntu 的 WSL 集成
- 终端启动早于 Docker Desktop 完全就绪

处理方式：

1. 确认 Windows 侧 Docker Desktop 已 Running
2. 重开一个 Ubuntu 终端
3. 再次执行 `docker info`

### 13.2 `./scripts/fisco-up.sh` 提示端口占用

常见占用端口：

- `20200-20203`
- `30300-30303`

处理方式：

```bash
./scripts/fisco-down.sh
docker ps
ss -ltn | grep -E ':2020[0-3]|:3030[0-3]'
```

如果仍被其他程序占用，先释放端口后再启动。

### 13.3 `openclaw` 命令不存在

说明 OpenClaw 没安装好，或者没有加入当前 shell 的 `PATH`。

先执行：

```bash
which openclaw
openclaw --version
```

如果找不到命令，就回到 OpenClaw 安装步骤修复。

### 13.4 `openclaw health` 失败

说明 OpenClaw 本身未就绪，不是 OpenQ 主工程的问题。

先单独把 OpenClaw 服务拉起来，再重新执行：

```bash
./scripts/openclaw-check.sh
```

### 13.5 Windows 浏览器打不开 `http://localhost:8000`

先在 Ubuntu 中检查：

```bash
curl -I http://127.0.0.1:8000
ss -ltnp | grep ':8000'
```

如果 Ubuntu 内部能访问，但 Windows 不行，优先检查：

- `uvicorn` 是否绑定到 `0.0.0.0`
- WSL2 端口转发是否正常
- Windows 防火墙是否拦截

### 13.6 一键脚本退出，但页面还是打不开

优先查看：

- `logs/openq-up.log`
- `logs/openq-app.log`
- `logs/openclaw.log`

一般能直接看出是：

- Docker 没起来
- OpenClaw health 失败
- FastAPI 启动异常

## 14. 交付建议

如果这台 Windows 设备是答辩或演示专用机，建议提前完成下面动作：

1. 在 WSL2 中完整跑通一次 `./scripts/openq-up.sh`
2. 在 Windows 浏览器中确认网页可打开
3. 用 `./scripts/preflight.sh` 和 `./scripts/acceptance.sh` 做一次部署后自检
4. 保留 `logs/` 目录，便于现场排查
5. 演示前先启动 Docker Desktop，再进入 WSL2 启动项目

## 依据来源

### 本地文档

- `README.md`
- `docs/部署与迁移说明.md`
- `docs/Windows部署说明.md`

### 仓库脚本与配置

- `scripts/openq-up.sh`
- `scripts/openq-down.sh`
- `scripts/fisco-up.sh`
- `scripts/fisco-status.sh`
- `scripts/openclaw-check.sh`
- `scripts/preflight.sh`
- `scripts/acceptance.sh`
- `app/core/settings.py`

### 运行资产

- `runtime-deps/fisco-portable`
- `runtime-deps/images/fiscobcos-v3.6.0-image.tar`
- `runtime-deps/packages/openclaw-2026.4.5.tgz`
