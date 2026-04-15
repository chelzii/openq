## openq

这个仓库现在已经收敛为单一主工程：`OpenClaw + 统一网关 + 虚拟 App + 双层防御 + 演示端 + 实验导出`。

- 需求终稿: `docs/最终版需求文档.md`
- 实现终稿: `docs/最终版实现文档.md`
- 迁移与部署: `docs/部署与迁移说明.md`
- Windows 部署: `docs/Windows部署说明.md`

当前目录职责：

- `app`：正式主工程，包含 API、网关、虚拟 App、双层防御、状态保护和演示页
- `data`：场景样本、状态文件、审计输出、实验导出
- `docs`：需求、实现、论文材料、部署说明
- `prototypes`：三个历史原型，仅作参考和迁移来源
- `runtime-deps`：`FISCO BCOS` 运行资产、镜像、控制台、SDK
- `scripts`：常用启动、停止、检查脚本
- `archive`：不参与当前实现的归档文件

常用命令:

```bash
./scripts/openq-up.sh
./scripts/openq-down.sh
./scripts/preflight.sh
./scripts/acceptance.sh
./scripts/run_experiments.sh
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/uvicorn app.main:app --reload
./scripts/fisco-up.sh
./scripts/fisco-status.sh
./scripts/fisco-console.sh
./scripts/fisco-down.sh
./scripts/openclaw-check.sh
```

其中 `./scripts/openq-up.sh` 是推荐的一键启动入口，会先检查或启动 FISCO BCOS，再检查或拉起 OpenClaw gateway，最后拉起 FastAPI 演示端。脚本会在确认服务可用后退出，服务本身继续后台运行。
`./scripts/openq-down.sh` 会按 PID、端口和进程名的顺序关停这一整套演示环境，优先清理本仓库启动的服务实例。

主工程能力：

- `GET /`：网页演示台
- `POST /api/demo/run`：运行单个场景
- `POST /api/experiments/run`：批量跑三类实验与三模式消融
- `POST /api/state/update`：演示受保护状态修改、审批与回滚
- `GET /api/chain/status`：查看 `FISCO BCOS` 实时探测状态和最新块高

当前默认实现说明：

- 第一层防御为结构化信任分区 + `BAAI/bge-base-zh-v1.5` Embedding 一判 + `BAAI/bge-reranker-v2-m3` 二判复核，阈值由 `data/fixtures/intent_calibration.json` 标定并落盘到 `data/baselines/guard_calibration.json`，需要时可用 `guard_threshold_override` 做受控覆盖
- 两套护栏模型权重已经内置在 `runtime-deps/models/BAAI/bge-base-zh-v1.5` 和 `runtime-deps/models/BAAI/bge-reranker-v2-m3`，正常部署时不需要再额外下载
- 第二层为 `FISCO BCOS` 实时链状态探测 + 本地 DID/权限注册表，`full` 模式下所有请求都会携带签名、验签、验权和链锚定审计信息
- `FISCO BCOS` 启动脚本已改为显式暴露 `20200-20203` 和 `30300-30303` 端口，console 会自动补齐配置并优先使用仓库内 `JDK 11`
- `OpenClaw` 默认走 `real_strict` 真实规划模式；连接失败或输出非法计划时直接返回 `planning_failed`
- `real_debug` 会保留 OpenClaw 原始输出、`raw_response` 和诊断信息，但不会进入执行链路
- `state.update_*` 已改为结构化 `patch` / `template_update` 参数，不再接受 planner 直接提交整段 `content`
- 仅在显式选择 `demo_safe` 调试保底模式时，系统才会复放场景内置 `debug_plan`

DashScope 本地配置：

- 仓库内预留了 `config/dashscope.yaml`，优先从这里读取 `api_key`
- 如果 YAML 里没有值，代码会回退读取环境变量 `DASHSCOPE_API_KEY`
- 也可以用 `DASHSCOPE_CONFIG_PATH` 指向其他 YAML 文件
