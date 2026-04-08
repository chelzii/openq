## openq

这个仓库现在已经收敛为单一主工程：`OpenClaw + 统一网关 + 虚拟 App + 双层防御 + 演示端 + 实验导出`。

- 需求终稿: [最终版需求文档.md](/home/chelizi/project/openq/docs/最终版需求文档.md)
- 实现终稿: [最终版实现文档.md](/home/chelizi/project/openq/docs/最终版实现文档.md)
- 迁移与部署: [部署与迁移说明.md](/home/chelizi/project/openq/docs/部署与迁移说明.md)
- Windows 部署: [Windows部署说明.md](/home/chelizi/project/openq/docs/Windows部署说明.md)

当前目录职责：

- [app](/home/chelizi/project/openq/app)：正式主工程，包含 API、网关、虚拟 App、双层防御、状态保护和演示页
- [data](/home/chelizi/project/openq/data)：场景样本、状态文件、审计输出、实验导出
- [docs](/home/chelizi/project/openq/docs)：需求、实现、论文材料、部署说明
- [prototypes](/home/chelizi/project/openq/prototypes)：三个历史原型，仅作参考和迁移来源
- [runtime-deps](/home/chelizi/project/openq/runtime-deps)：`FISCO BCOS` 运行资产、镜像、控制台、SDK
- [scripts](/home/chelizi/project/openq/scripts)：常用启动、停止、检查脚本
- [archive](/home/chelizi/project/openq/archive)：不参与当前实现的归档文件

常用命令:

```bash
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/uvicorn app.main:app --reload
./scripts/fisco-up.sh
./scripts/fisco-status.sh
./scripts/fisco-console.sh
./scripts/fisco-down.sh
./scripts/openclaw-check.sh
```

主工程能力：

- `GET /`：网页演示台
- `POST /api/demo/run`：运行单个场景
- `POST /api/experiments/run`：批量跑三类实验与三模式消融
- `POST /api/state/update`：演示受保护状态修改、审批与回滚
- `GET /api/chain/status`：查看 `FISCO BCOS` 实时探测状态和最新块高

当前默认实现说明：

- 第一层防御为结构化信任分区 + `BAAI/bge-base-zh-v1.5` Embedding 一判 + `BAAI/bge-reranker-v2-m3` 二判复核，阈值由 `data/fixtures/intent_calibration.json` 标定并落盘到 `data/baselines/guard_calibration.json`，需要时可用 `guard_threshold_override` 做受控覆盖
- 第二层为 `FISCO BCOS` 实时链状态探测 + 本地 DID/权限注册表，`full` 模式下所有请求都会携带签名、验签、验权和链锚定审计信息
- `FISCO BCOS` 启动脚本已改为显式暴露 `20200-20203` 和 `30300-30303` 端口，console 会自动补齐配置并优先使用仓库内 `JDK 11`
- `OpenClaw` 支持真实 WebSocket 连接；未连接时自动回退到本地演示回复
