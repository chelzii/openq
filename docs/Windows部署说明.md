# OpenQ Windows 部署说明

这份文档面向 Windows 10/11 用户，给出从零开始的完整部署路径。

推荐方案是 `Windows + WSL2 + Ubuntu + Docker Desktop`，然后把项目、Python 运行环境、FISCO BCOS 脚本都放在 `WSL2` 里执行。这样最接近仓库当前的实际运行方式，也最稳定。

如果直接在 `PowerShell` 或 `CMD` 里跑这些脚本，通常会遇到路径、权限、证书、`bash` 行为不一致的问题，所以不建议。

## 1. 这个项目在 Windows 上怎么跑

OpenQ 当前主工程由下面几部分组成：

| 组件 | 运行位置 | 说明 |
| --- | --- | --- |
| `FastAPI` 主服务 | `WSL2 Ubuntu` | 提供网页演示和全部 `API` |
| `FISCO BCOS` | `Docker Desktop` | 通过仓库内的运行资产启动 4 节点链 |
| `OpenClaw` | 推荐装在 `WSL2 Ubuntu` | 通过 `ws://localhost:18789` 连接 |
| 研究数据和状态文件 | `WSL2 Ubuntu` 的项目目录 | `data/` 下的审计、记忆、基线、样本都在这里 |

主服务默认会访问这些端口：

| 功能 | 默认地址 |
| --- | --- |
| 网页演示 | `http://localhost:8000` |
| OpenClaw WebSocket | `ws://localhost:18789` |
| FISCO 节点 | `20200` 到 `20203` |
| FISCO RPC / 监听端口 | `30300` 到 `30303` |

## 2. 部署前准备

### 2.1 硬件和系统建议

建议准备至少 `16 GB` 内存，`30 GB` 以上可用磁盘空间，`4` 核以上 CPU。

如果你的机器资源比较紧张，项目也能跑，但 `torch`、`sentence-transformers` 和 `Docker Desktop` 会让启动变慢。

### 2.2 推荐工作目录

请把仓库放在 `WSL2` 的 Linux 文件系统里，例如 `~/project/openq`。

不要放在这些位置：

| 位置 | 原因 |
| --- | --- |
| `C:\` 挂载盘，比如 `/mnt/c/...` | 文件系统性能和权限表现不稳定 |
| OneDrive 同步目录 | 容易被同步程序占用 |
| 桌面临时目录 | 后续维护和脚本执行都不方便 |

### 2.3 环境变量

项目默认会读取下面几个环境变量：

| 变量 | 推荐值 | 作用 |
| --- | --- | --- |
| `OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD` | `0` | 是否允许首次在线下载意图护栏模型 |
| `OPENQ_GUARD_DEVICE` | `cpu` | 护栏模型运行设备，默认最稳妥 |
| `OPENQ_GUARD_RUNTIME` | `formal` | 使用正式模型后端，找不到模型时会自动回退 |

如果你第一次启动时没有模型缓存，可以临时把 `OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD` 改成 `1`。  
如果你保持 `0`，系统也能运行，只是会自动回退到仓库内的 hashing fallback。

## 3. 从零开始安装

### 第一步，开启 WSL2

在 Windows 上打开 `PowerShell` 管理员窗口，执行：

```powershell
wsl --install
wsl --set-default-version 2
```

执行完以后重启电脑。

如果你已经装过 `WSL`，只需要确认当前发行版是 `WSL2`。

### 第二步，安装 Ubuntu

可以通过 Microsoft Store 安装 `Ubuntu`，也可以直接在 `PowerShell` 里执行：

```powershell
wsl --install -d Ubuntu
```

安装完成后，打开 Ubuntu，创建 Linux 用户名和密码。

### 第三步，安装基础工具

在 `Ubuntu` 里执行：

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git curl unzip ca-certificates build-essential
```

这一步的作用是准备后续拉仓库、安装 `uv` 和运行脚本所需的基础工具。

### 第四步，安装 Docker Desktop

在 Windows 主机上安装 `Docker Desktop`，安装后完成这几项设置：

1. 启用 `Use the WSL 2 based engine`
2. 打开 `Resources` 里的 `WSL Integration`
3. 勾选你正在使用的 `Ubuntu` 发行版
4. 给 `Docker Desktop` 分配足够内存，建议至少 `6 GB`

安装完后先启动一次 `Docker Desktop`，确认右下角状态正常。

仓库里的 `FISCO BCOS` 依赖 Docker，所以这一步必须先完成。

### 第五步，安装 OpenClaw

OpenClaw 的具体安装方式以官方文档为准，因为它的安装包和版本会随官方发布更新。

官方入口如下：

`https://docs.openclaw.ai/install`

`https://docs.openclaw.ai/start/setup`

安装完成后，在你准备用来跑项目的环境里确认：

```bash
openclaw --version
openclaw health
```

当前项目代码默认连接的是 `ws://localhost:18789`。  
如果你的 OpenClaw 监听端口不是这个值，要么把 OpenClaw 改到这个端口，要么修改 `app/core/settings.py` 里的 `real_openclaw_url`。

### 第六步，安装 `uv`

在 `Ubuntu` 里安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv --version
```

`uv` 是这个项目推荐的 Python 入口，后面所有依赖安装和运行都通过它完成。

### 第七步，准备 Python 3.12

项目要求 `Python 3.12`。推荐直接让 `uv` 管理 Python：

```bash
uv python install 3.12
```

如果这一步成功，后面 `uv sync` 会自动使用对应版本。

## 4. 获取项目代码

把仓库放到 `WSL2` 的 Linux 文件系统里，例如：

```bash
mkdir -p ~/project
cd ~/project
git clone <你的仓库地址> openq
cd openq
```

如果仓库已经复制到本机，也可以直接进入项目目录，只要它最终位于 `WSL2` 的 Linux 文件系统中即可。

建议你现在确认一下当前目录确实是项目根目录，并且能看到 `app/`、`data/`、`scripts/`、`runtime-deps/` 这些目录。

## 5. 安装项目依赖

在项目根目录执行：

```bash
uv sync
```

这一步会根据 `pyproject.toml` 和 `uv.lock` 创建虚拟环境并安装依赖。

完成后可以快速确认 Python 入口正常：

```bash
uv run python -V
uv run python -m unittest discover -s tests -v
```

如果你不想让护栏模块首次联网下载模型，就继续保持前面设置的：

```bash
export OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD=0
export OPENQ_GUARD_DEVICE=cpu
export OPENQ_GUARD_RUNTIME=formal
```

如果你希望首次启动就下载正式模型，可以临时执行：

```bash
export OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD=1
```

## 6. 启动 FISCO BCOS

先确认 `Docker Desktop` 已经启动。

然后在项目根目录执行：

```bash
./scripts/fisco-up.sh
```

这个脚本会自动做几件事：

1. 检查仓库内的链运行目录是否存在
2. 检查 Docker 是否可用
3. 如果本地没有 `fiscoorg/fiscobcos:v3.6.0` 镜像，就从仓库里的 `runtime-deps/images/fiscobcos-v3.6.0-image.tar` 导入
4. 创建专用 Docker 网络 `openq-fisco`
5. 启动仓库内的 4 个节点

启动后执行：

```bash
./scripts/fisco-status.sh
```

正常情况下，你会看到运行中的 FISCO 容器、SDK 证书目录和关键端口监听信息。

如果你想进入链控制台，可以执行：

```bash
./scripts/fisco-console.sh
```

这个脚本会优先使用仓库自带的 `JDK 11`，路径是 `runtime-deps/jdks/temurin-11`，所以通常不需要你再额外安装 Java。

停止链服务的命令是：

```bash
./scripts/fisco-down.sh
```

### FISCO 启动失败时先看什么

| 现象 | 先检查什么 |
| --- | --- |
| `docker` 不可用 | `Docker Desktop` 是否已启动，WSL 集成是否打开 |
| 端口被占用 | `20200` 到 `20203`，以及 `30300` 到 `30303` 是否已有别的链或容器占用 |
| `console_connect_failed` | `Docker Desktop` 是否正常，链是否真的启动成功 |
| `chain unavailable` | `fisco-up.sh` 是否执行成功，`fisco-status.sh` 是否能看到节点 |

## 7. 检查 OpenClaw

在 `WSL2` 里执行：

```bash
openclaw --version
openclaw health
```

如果命令不存在，说明 OpenClaw 还没有装好，或者没有加入当前终端的 `PATH`。

项目的 OpenClaw 连接地址默认是 `ws://localhost:18789`。  
如果你的 OpenClaw 运行在 Windows 主机上，一般也可以通过 `localhost` 访问，但前提是 Windows 到 `WSL2` 的本地回环转发正常。

如果这里连不上，优先检查这三点：

1. OpenClaw 是否真的启动
2. 18789 端口是否被其他程序占用
3. 你的 OpenClaw 是否和项目运行在同一个网络可见范围内

## 8. 启动 OpenQ 主服务

在项目根目录执行：

```bash
export OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD=0
export OPENQ_GUARD_DEVICE=cpu
export OPENQ_GUARD_RUNTIME=formal

uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

启动成功后，在 Windows 浏览器里访问：

`http://localhost:8000`

如果页面打不开，先确认下面两件事：

1. `uvicorn` 进程是否还在运行
2. Windows 的 `localhost` 是否能转发到 `WSL2`

如果你更习惯固定命令，也可以直接使用仓库根目录 `README.md` 里的启动方式，只要确保在 `WSL2` 里执行即可。

如果你想直接一键启动整套本地演示环境，也可以在 `WSL2` 终端里执行：

```bash
./scripts/openq-up.sh
./scripts/openq-down.sh
```

这个命令会自动处理 FISCO BCOS、OpenClaw gateway 和 `FastAPI` 演示端，脚本确认服务可用后会直接退出，适合答辩现场快速拉起环境。
如果要关停整套环境，执行 `./scripts/openq-down.sh` 即可。

## 9. 部署后的验证顺序

建议按下面顺序确认：

```bash
./scripts/preflight.sh
```

这个脚本会一次性检查：

1. 护栏模型状态
2. FISCO 运行状态
3. OpenClaw 命令可用性
4. FastAPI 应用装载是否正常

如果你想做完整验收，再执行：

```bash
./scripts/acceptance.sh
```

这个脚本会继续跑语法检查、单元测试和核心场景验证。

如果你只是想确认项目可以交付运行，最少要看这三个结果：

| 检查项 | 期望结果 |
| --- | --- |
| `./scripts/fisco-status.sh` | 能看到节点和端口监听 |
| `openclaw health` | OpenClaw 返回健康状态 |
| `http://localhost:8000` | 页面能打开并显示演示界面 |

## 10. 浏览器里的日常使用

打开主页后，你可以直接看到演示面板。常用操作顺序是：

1. 选择 `off`、`guard_only` 或 `full`
2. 选择一个场景
3. 点击运行
4. 观察请求链路、拦截层级、审计记录和链状态

当前项目的三种模式定义是固定的：

| 模式 | 含义 |
| --- | --- |
| `off` | 关闭双层防御，只保留最基础的执行和记录 |
| `guard_only` | 只启用第一层意图护栏 |
| `full` | 第一层护栏加第二层 FISCO BCOS 验签验权 |

## 11. 常见问题

### 11.1 `openclaw` 命令找不到

说明 OpenClaw 没有装好，或者终端没有读到它的安装路径。

先在 `WSL2` 里执行：

```bash
openclaw --version
```

如果还是找不到，就按官方文档重新安装，安装完成后重新打开一个终端窗口再试。

### 11.2 `fisco-up.sh` 报端口占用

说明本机上已经有别的链服务、Docker 容器，或者旧实例还没停干净。

先执行：

```bash
./scripts/fisco-down.sh
```

然后再看 `docker ps`，确认 `20200` 到 `20203`、`30300` 到 `30303` 都已经释放。

### 11.3 页面能开，但 `FISCO` 显示不可用

通常是 Docker、链节点、控制台任意一个环节没有起来。

按这个顺序排查：

1. `Docker Desktop` 是否运行
2. `./scripts/fisco-status.sh` 是否能看到容器
3. `./scripts/fisco-console.sh` 是否能连上控制台

### 11.4 护栏模型没有下载

这不一定是错误。

如果你把 `OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD` 设成了 `0`，项目会优先使用离线 fallback，所以系统仍然可以启动。

如果你希望正式模型参与判断，就把这个变量临时设成 `1`，再重新启动服务。

### 11.5 `localhost:8000` 打不开

先检查 `uvicorn` 是否在跑。  
再检查 `WSL2` 的本地回环是否正常。  
如果你同时开了多个网络代理、VPN 或安全软件，也可能影响回环访问。

### 11.6 仓库放在 `C:\` 下后脚本很慢

这是 `WSL2` 下最常见的问题之一。  
建议直接把仓库迁移到 `~/project/openq` 这种 Linux 路径下，然后重新跑 `uv sync` 和 `./scripts/fisco-up.sh`。

## 12. 停止和重启

关闭主服务时，先在运行 `uvicorn` 的终端按 `Ctrl+C`。

然后如果你要停链，再执行：

```bash
./scripts/fisco-down.sh
```

如果你连 OpenClaw 也要一起关掉，就按它自己的官方方式停止。

下次重启时，顺序仍然建议是：

1. 启动 `Docker Desktop`
2. 启动 `FISCO BCOS`
3. 确认 `OpenClaw`
4. 启动 `uvicorn`

这样最稳。

## 13. 这套 Windows 部署的结论

如果你按上面的 `WSL2` 路径部署，这个项目在 Windows 上是可以完整运行的。

最关键的不是 Windows 本身，而是这三件事：

1. 项目脚本在 `WSL2 Ubuntu` 里执行
2. `Docker Desktop` 正常提供链服务
3. `OpenClaw` 能通过 `ws://localhost:18789` 连上

这三件事都通了，OpenQ 的网页演示、链状态、受保护状态更新、实验脚本和验收脚本就都能跑起来。
