"""
文件完整性监控脚本 (File Integrity Monitor)
=============================================
防止智能体在交互过程中对 MEMORY.md 等关键文件进行恶意篡改。

功能：
  1. check  - Agent 调用前校验文件哈希，不一致则拦截并等待人工确认
  2. watch  - 持续监控目录，自动记录变更并生成链式审计日志
  3. init   - 为指定文件/目录初始化基准哈希
  4. status - 显示所有受监控文件的当前状态

用法：
  python file_integrity_monitor.py init   [文件或目录...]
  python file_integrity_monitor.py check  <文件路径>
  python file_integrity_monitor.py watch  [目录路径] [--interval 秒数]
  python file_integrity_monitor.py status
"""

import os
import sys
import hashlib
import time
import json
import difflib
import argparse
from datetime import datetime

# ============================================================
# 配置
# ============================================================
BASELINE_FILE = "hash_baseline.json"      # 基准哈希存储
AUDIT_LOG_FILE = "integrity_audit.jsonl"   # 链式审计日志 (JSON Lines)
DEFAULT_WATCH_DIR = "dev_portal"           # 默认监控目录
DEFAULT_INTERVAL = 5                       # 默认扫描间隔（秒）
POST_INPUT_MONITOR_SECONDS = 6             # 用户输入后继续监听时长（秒）
REFACTOR_PENDING_FLAG = ".memory_refactor_pending"  # memory_refactor 确认中的标记文件

# 需要严格保护的文件 —— check 模式会阻断并等待人工确认
PROTECTED_FILES = [
    "MEMORY.md",
]

# 监控的文件扩展名
WATCHED_EXTENSIONS = {".md", ".json", ".yaml", ".yml", ".txt", ".py"}

# 文件内容快照目录（用于计算 diff）
SNAPSHOTS_DIR = ".baseline_snapshots"


# ============================================================
# 核心：哈希计算
# ============================================================
def calculate_hash(filepath):
    """计算文件的 SHA-256 哈希值。"""
    if not os.path.isfile(filepath):
        return None
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


# ============================================================
# 基准哈希读写
# ============================================================
def load_baseline():
    if not os.path.exists(BASELINE_FILE):
        return {}
    with open(BASELINE_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def save_baseline(data):
    with open(BASELINE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ============================================================
# 文件内容快照（用于 diff 对比）
# ============================================================
def _snapshot_path(filepath):
    """根据原始文件路径生成快照存储路径（init/check 使用）。"""
    safe_name = filepath.replace(os.sep, "__").replace(":", "")
    return os.path.join(SNAPSHOTS_DIR, safe_name)


def _watch_snapshot_path(filepath):
    """根据原始文件路径生成 watch 专用快照路径。"""
    safe_name = filepath.replace(os.sep, "__").replace(":", "")
    return os.path.join(SNAPSHOTS_DIR, "watch__" + safe_name)


def save_snapshot(filepath):
    """保存文件当前内容的快照副本。"""
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    sp = _snapshot_path(filepath)
    with open(filepath, "r", encoding="utf-8", errors="replace") as src:
        content = src.read()
    with open(sp, "w", encoding="utf-8") as dst:
        dst.write(content)


def load_snapshot(filepath):
    """加载文件的快照内容，返回行列表。如无快照返回空列表。"""
    sp = _snapshot_path(filepath)
    if not os.path.exists(sp):
        return []
    with open(sp, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def save_watch_snapshot(filepath):
    """保存 watch 专用快照（不影响 init/check 使用的主快照）。"""
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    sp = _watch_snapshot_path(filepath)
    with open(filepath, "r", encoding="utf-8", errors="replace") as src:
        content = src.read()
    with open(sp, "w", encoding="utf-8") as dst:
        dst.write(content)


def load_watch_snapshot(filepath):
    """加载 watch 专用快照，如无则回退到主快照。"""
    sp = _watch_snapshot_path(filepath)
    if os.path.exists(sp):
        with open(sp, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    # 回退到主快照
    return load_snapshot(filepath)


def restore_from_main_snapshot(filepath):
    """将文件内容恢复为主快照（init/check 使用的基线版本）。"""
    sp = _snapshot_path(filepath)
    if not os.path.exists(sp):
        return False
    with open(sp, "r", encoding="utf-8", errors="replace") as src:
        content = src.read()
    with open(filepath, "w", encoding="utf-8") as dst:
        dst.write(content)
    return True


def show_diff(filepath, use_watch_snapshot=False):
    """
    对比文件的基准快照与当前内容，打印 unified diff。
    use_watch_snapshot=True 时使用 watch 专用快照。
    返回是否存在差异。
    """
    old_lines = load_watch_snapshot(filepath) if use_watch_snapshot else load_snapshot(filepath)
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        new_lines = f.readlines()

    if not old_lines:
        print(f"\n  (无基准快照，显示当前文件完整内容)")
        for i, line in enumerate(new_lines, 1):
            print(f"  + {i:4d} | {line}", end="")
        print()
        return True

    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"{filepath} (基准版本)",
        tofile=f"{filepath} (当前版本)",
        lineterm="",
    ))

    if not diff:
        print("  (无差异)")
        return False

    print(f"\n  {'─'*60}")
    print(f"  文件变更详情:")
    print(f"  {'─'*60}")
    for line in diff:
        line = line.rstrip("\n")
        if line.startswith("+++ ") or line.startswith("--- "):
            print(f"  {line}")
        elif line.startswith("+"):
            print(f"  \033[32m{line}\033[0m")   # 绿色：新增
        elif line.startswith("-"):
            print(f"  \033[31m{line}\033[0m")   # 红色：删除
        elif line.startswith("@@"):
            print(f"  \033[36m{line}\033[0m")   # 青色：位置
        else:
            print(f"  {line}")
    print(f"  {'─'*60}\n")
    return True


def monitor_after_user_input(filepath, baseline_hash=None, seconds=POST_INPUT_MONITOR_SECONDS):
    """用户输入完成后继续监听一段时间，再返回主流程。"""
    filepath = os.path.normpath(filepath)
    print(f"  [后续监听] 将继续观察 {filepath} {seconds} 秒...")
    last_hash = None
    changed_count = 0
    start = time.time()

    while time.time() - start < seconds:
        current_hash = calculate_hash(filepath)
        if current_hash is None:
            print("  [后续监听] 文件不存在，结束观察。")
            return None

        if last_hash is not None and current_hash != last_hash:
            changed_count += 1

        last_hash = current_hash
        time.sleep(1)

    if baseline_hash is not None:
        if last_hash == baseline_hash:
            print("  [后续监听结果] 文件与基线一致，状态稳定。")
        else:
            print("  [后续监听结果] 文件仍与基线不一致，状态稳定。")
    else:
        print("  [后续监听结果] 观察完成。")

    if changed_count > 0:
        print(f"  [后续监听结果] 观察期间检测到 {changed_count} 次哈希变化。")
    return last_hash


# ============================================================
# 链式审计日志
# ============================================================
def _last_log_hash():
    """读取审计日志最后一条记录的哈希，用于构建链式关联。"""
    if not os.path.exists(AUDIT_LOG_FILE):
        return "0" * 64  # 创世块
    last_line = ""
    with open(AUDIT_LOG_FILE, "rb") as f:
        # 从文件末尾向前查找最后一行
        try:
            f.seek(-2, 2)
            while f.read(1) != b"\n":
                f.seek(-2, 1)
            last_line = f.readline().decode("utf-8").strip()
        except OSError:
            f.seek(0)
            lines = f.readlines()
            if lines:
                last_line = lines[-1].decode("utf-8").strip()
    if not last_line:
        return "0" * 64
    try:
        entry = json.loads(last_line)
        return entry.get("entry_hash", "0" * 64)
    except json.JSONDecodeError:
        return "0" * 64


def append_audit_log(filepath, old_hash, new_hash, action="modified"):
    """
    追加一条链式审计日志。
    每条记录包含前一条记录的哈希，形成链式时间序列。
    """
    prev_hash = _last_log_hash()
    timestamp = datetime.now().isoformat()

    entry_content = {
        "timestamp": timestamp,
        "file": filepath,
        "action": action,
        "old_hash": old_hash,
        "new_hash": new_hash,
        "prev_entry_hash": prev_hash,
    }
    # 计算本条记录自身的哈希，作为下一条记录的 prev
    raw = json.dumps(entry_content, sort_keys=True, ensure_ascii=False)
    entry_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    entry_content["entry_hash"] = entry_hash

    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry_content, ensure_ascii=False) + "\n")

    return entry_content


# ============================================================
# 核心功能 1：文件完整性校验（Agent 调用前拦截）
# ============================================================
def check_file_integrity(filepath, interactive=True):
    """
    校验单个文件的哈希完整性。

    返回值：
        True  - 文件未被篡改 或 用户确认接受变更
        False - 文件被篡改且用户拒绝
    """
    filepath = os.path.normpath(filepath)
    print(f"\n{'='*60}")
    print(f"  文件完整性校验: {filepath}")
    print(f"{'='*60}")

    baseline = load_baseline()
    current_hash = calculate_hash(filepath)

    if current_hash is None:
        print(f"  [错误] 文件不存在: {filepath}")
        return False

    # 首次校验 —— 自动注册基准哈希并保存快照
    if filepath not in baseline:
        baseline[filepath] = {
            "hash": current_hash,
            "registered_at": datetime.now().isoformat(),
        }
        save_baseline(baseline)
        save_snapshot(filepath)
        append_audit_log(filepath, "N/A", current_hash, action="registered")
        print(f"  [初始化] 已注册基准哈希: {current_hash[:16]}...")
        print(f"  [初始化] 已保存内容快照")
        print(f"  [结果] 校验通过 ✓")
        return True

    baseline_hash = baseline[filepath]["hash"]

    if current_hash == baseline_hash:
        print(f"  [基准哈希] {baseline_hash[:16]}...")
        print(f"  [当前哈希] {current_hash[:16]}...")
        print(f"  [结果] 校验通过 ✓ — 文件未被修改")
        return True

    # ---- 哈希不一致：检测到文件变更 ----
    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print(f"  ║  !! {filepath} 的哈希值已发生改变 !!                   ")
    print("  ╠══════════════════════════════════════════════════════════╣")
    print(f"  ║  基准哈希: {baseline_hash}")
    print(f"  ║  当前哈希: {current_hash}")
    print("  ╠══════════════════════════════════════════════════════════╣")
    print("  ║  文件内容已被修改，操作已拦截，请立即做出决定！         ")
    print("  ╚══════════════════════════════════════════════════════════╝")

    # 自动展示具体变更内容
    print("\n  以下是文件发生的具体变更:")
    show_diff(filepath)

    append_audit_log(filepath, baseline_hash, current_hash, action="tamper_detected")

    if not interactive:
        print("  [非交互模式] 自动拒绝变更。")
        return False

    # 交互式确认
    while True:
        print("  是否接受此变更？")
        print("    [a] 接受变更 — 信任当前内容并更新基准哈希")
        print("    [r] 拒绝变更 — 阻止本次调用")
        print("    [d] 再次查看差异")
        choice = input("  > ").strip().lower()

        if choice == "a":
            baseline[filepath] = {
                "hash": current_hash,
                "registered_at": datetime.now().isoformat(),
                "accepted_from": baseline_hash,
            }
            save_baseline(baseline)
            save_snapshot(filepath)
            append_audit_log(filepath, baseline_hash, current_hash, action="user_accepted")
            print("  [已接受] 基准哈希与快照已更新。")
            return True

        elif choice == "r":
            append_audit_log(filepath, baseline_hash, current_hash, action="user_rejected")
            print("  [已拒绝] 调用流程已阻断。")
            return False

        elif choice == "d":
            show_diff(filepath)

        else:
            print("  无效选择，请输入 a / r / d")


# ============================================================
# 核心功能 2：持续目录监控
# ============================================================
def watch_directory(watch_dir, interval):
    """
    周期性扫描目录和受保护文件，检测变更并记录链式审计日志。
    同时监控 PROTECTED_FILES 中的文件，一经变化立即通知用户。
    """
    watch_dir = os.path.normpath(watch_dir)
    print(f"\n{'='*60}")
    print(f"  目录监控已启动")
    print(f"  监控路径: {watch_dir}")
    print(f"  受保护文件: {', '.join(PROTECTED_FILES)}")
    print(f"  扫描间隔: {interval} 秒")
    print(f"  审计日志: {AUDIT_LOG_FILE}")
    print(f"  按 Ctrl+C 停止")
    print(f"{'='*60}\n")

    scan_count = 0
    warned_untracked = set()
    # 受保护文件的“修改迹象”缓存：仅在连续两次扫描仍异常时才要求用户决策
    pending_protected = {}

    try:
        while True:
            scan_count += 1
            baseline = load_baseline()
            changes_in_scan = 0

            if not os.path.exists(watch_dir):
                print(f"  [扫描 #{scan_count}] 目录不存在: {watch_dir}，等待...")
                time.sleep(interval)
                continue

            # ---- 首先检查受保护文件（如 MEMORY.md）----
            for pf in PROTECTED_FILES:
                pf = os.path.normpath(pf)
                if not os.path.isfile(pf):
                    continue
                current_hash = calculate_hash(pf)
                if not current_hash:
                    continue

                if pf not in baseline:
                    # 仅监控已初始化到基线的受保护文件，避免未初始化文件被自动纳入
                    if pf not in warned_untracked:
                        print(f"  [提示] {pf} 未初始化基准哈希，watch 将跳过该文件。")
                        print(f"         如需监控，请先执行: python file_integrity_monitor.py init {pf}")
                        warned_untracked.add(pf)
                    continue

                else:
                    # 用 watch_ack_hash 判断是否需要再次提醒
                    # （如果用户已在 watch 中确认过此哈希，就不再重复提醒）
                    ack_hash = baseline[pf].get("watch_ack_hash", baseline[pf]["hash"])
                    if ack_hash != current_hash:
                        # 若重构脚本仍在等待用户确认，则仅记录“确认中迹象”，不弹出决策菜单
                        if os.path.exists(REFACTOR_PENDING_FLAG):
                            state = pending_protected.get(pf)
                            if state is None or state.get("hash") != current_hash:
                                pending_protected[pf] = {
                                    "hash": current_hash,
                                    "count": 1,
                                    "first_seen": datetime.now().isoformat(),
                                    "pending": True,
                                }
                                print(f"\n  [迹象] 检测到 {pf} 变化，但当前处于 memory_refactor 用户确认阶段。")
                                print("        暂不要求选择，等待确认结果...")
                                append_audit_log(pf, baseline[pf]["hash"], current_hash, action="pending_confirmation_trace")
                            continue

                        # 第一阶段：先记为“修改迹象”，避免瞬时改动（随后回滚）触发误报交互
                        state = pending_protected.get(pf)
                        if state is None or state.get("hash") != current_hash:
                            pending_protected[pf] = {
                                "hash": current_hash,
                                "count": 1,
                                "first_seen": datetime.now().isoformat(),
                            }
                            print(f"\n  [迹象] 检测到 {pf} 出现改动迹象（哈希与基线不一致）。")
                            print("        将在下一次扫描确认是否为持续变更...")
                            append_audit_log(pf, baseline[pf]["hash"], current_hash, action="suspicious_trace")
                            continue

                        state["count"] = state.get("count", 1) + 1
                        pending_protected[pf] = state

                        # 第二阶段：连续两次扫描仍不一致，判定为持续变更，才要求用户决策
                        if state["count"] < 2:
                            continue

                        # 进入交互前再做一次实时校验，避免“已回滚但仍提示”的竞态
                        latest_hash = calculate_hash(pf)
                        if latest_hash == baseline[pf]["hash"]:
                            trace = pending_protected.pop(pf, None)
                            print(f"\n  [迹象消失] {pf} 已恢复与基线一致。")
                            print("           识别为短时改动后回滚，不再要求选择。")
                            append_audit_log(
                                pf,
                                (trace or {}).get("hash", "N/A"),
                                baseline[pf]["hash"],
                                action="trace_reverted",
                            )
                            continue
                        current_hash = latest_hash

                        # 受保护文件持续变更 —— 通知用户
                        init_hash = baseline[pf]["hash"]
                        ts = datetime.now().isoformat()
                        print(f"\n  ╔══════════════════════════════════════════════════════════╗")
                        print(f"  ║  !! {pf} 的哈希值持续异常，确认已发生变更 !!               ")
                        print(f"  ╠══════════════════════════════════════════════════════════╣")
                        print(f"  ║  时间: {ts}")
                        print(f"  ║  初始基准哈希: {init_hash}")
                        print(f"  ║  当前文件哈希: {current_hash}")
                        print(f"  ╠══════════════════════════════════════════════════════════╣")
                        print(f"  ║  文件内容已被修改，请立即做出决定！                       ")
                        print(f"  ╚══════════════════════════════════════════════════════════╝")
                        print(f"\n  以下是 {pf} 发生的具体变更:")
                        show_diff(pf, use_watch_snapshot=True)

                        append_audit_log(pf, init_hash, current_hash, action="tamper_detected")

                        # 立即询问用户
                        while True:
                            # 等待用户输入期间再次实时校验：若已回滚，自动结束交互
                            latest_hash = calculate_hash(pf)
                            if latest_hash == baseline[pf]["hash"]:
                                pending_protected.pop(pf, None)
                                print(f"  [已自动取消] {pf} 已恢复到基线，不再需要选择。\n")
                                append_audit_log(
                                    pf,
                                    current_hash,
                                    baseline[pf]["hash"],
                                    action="trace_reverted",
                                )
                                break

                            print(f"  是否确认已知悉 {pf} 的变更？")
                            print("    [a] 已知悉 — 不再重复提醒此次变更（check 仍会拦截）")
                            print("    [r] 暂不处理 — 下次扫描继续提醒")
                            print("    [b] 回溯恢复 — 恢复到初始化基线版本")
                            print("    [d] 再次查看差异")
                            choice = input("  > ").strip().lower()
                            if choice == "a":
                                # 只更新 watch_ack_hash，不修改 hash（check 基准不变）
                                baseline[pf]["watch_ack_hash"] = current_hash
                                save_watch_snapshot(pf)
                                save_baseline(baseline)
                                append_audit_log(pf, init_hash, current_hash, action="watch_acknowledged")
                                print(f"  [已知悉] 不再重复提醒此变更。")
                                print(f"  [注意] check 命令仍会检测到此变更并拦截。\n")
                                monitor_after_user_input(pf, baseline_hash=baseline[pf]["hash"])
                                changes_in_scan += 1
                                pending_protected.pop(pf, None)
                                break
                            elif choice == "r":
                                append_audit_log(pf, init_hash, current_hash, action="user_deferred")
                                print(f"  [暂不处理] 下次扫描将继续提醒。\n")
                                monitor_after_user_input(pf, baseline_hash=baseline[pf]["hash"])
                                pending_protected.pop(pf, None)
                                break
                            elif choice == "b":
                                restored = restore_from_main_snapshot(pf)
                                if not restored:
                                    print("  [失败] 未找到主快照，无法回溯恢复。")
                                    print("  请先执行 init 建立基线快照。")
                                    continue

                                restored_hash = calculate_hash(pf)
                                baseline[pf]["watch_ack_hash"] = restored_hash
                                save_watch_snapshot(pf)
                                save_baseline(baseline)
                                append_audit_log(pf, current_hash, restored_hash, action="rollback_restored")
                                print("  [已回溯] 文件已恢复到基线版本。")
                                print(f"  [回溯后哈希] {restored_hash}")
                                monitor_after_user_input(pf, baseline_hash=baseline[pf]["hash"])
                                changes_in_scan += 1
                                pending_protected.pop(pf, None)
                                break
                            elif choice == "d":
                                show_diff(pf, use_watch_snapshot=True)
                            else:
                                print("  无效选择，请输入 a / r / b / d")
                    else:
                        # 若此前有“迹象”，但当前已恢复一致，则仅记录为“已回滚迹象”，不触发交互
                        if pf in pending_protected:
                            trace = pending_protected.pop(pf)
                            print(f"\n  [迹象消失] {pf} 已恢复与基线一致。")
                            print("           推断为短时改动后回滚（例如你在弹窗中选择了“否”）。")
                            append_audit_log(
                                pf,
                                trace.get("hash", "N/A"),
                                baseline[pf]["hash"],
                                action="trace_reverted",
                            )

            # 收集当前目录中所有文件
            current_files = set()
            for root, _, files in os.walk(watch_dir):
                for name in files:
                    ext = os.path.splitext(name)[1].lower()
                    if ext in WATCHED_EXTENSIONS or not WATCHED_EXTENSIONS:
                        fp = os.path.normpath(os.path.join(root, name))
                        current_files.add(fp)

            # 检测新增和修改的文件
            for fp in sorted(current_files):
                current_hash = calculate_hash(fp)
                if not current_hash:
                    continue

                if fp not in baseline:
                    # 新文件 —— 注册并保存快照
                    baseline[fp] = {
                        "hash": current_hash,
                        "registered_at": datetime.now().isoformat(),
                    }
                    save_snapshot(fp)
                    entry = append_audit_log(fp, "N/A", current_hash, action="new_file")
                    ts = entry["timestamp"]
                    print(f"\n  [{ts}] 发现新文件: {fp}")
                    print(f"  已生成新哈希: {current_hash[:16]}...")
                    changes_in_scan += 1

                elif baseline[fp]["hash"] != current_hash:
                    # 文件被修改 —— 展示 diff 并询问用户
                    old_hash = baseline[fp]["hash"]
                    ts = datetime.now().isoformat()
                    print(f"\n  [{ts}] 检测到文件变更: {fp}")
                    print(f"  已生成新哈希: {current_hash[:16]}...")
                    print(f"  旧哈希:       {old_hash[:16]}...")
                    print(f"\n  以下是发生的具体变更:")
                    show_diff(fp)

                    # 询问用户是否接受
                    while True:
                        print(f"  是否接受 {fp} 的变更？")
                        print("    [a] 接受 — 更新基准哈希和快照")
                        print("    [r] 拒绝 — 保持原有基准（下次扫描仍会提醒）")
                        print("    [d] 再次查看差异")
                        choice = input("  > ").strip().lower()
                        if choice == "a":
                            baseline[fp] = {
                                "hash": current_hash,
                                "registered_at": datetime.now().isoformat(),
                                "previous_hash": old_hash,
                            }
                            save_snapshot(fp)
                            append_audit_log(fp, old_hash, current_hash, action="user_accepted")
                            print(f"  [已接受] {fp} 的基准哈希与快照已更新。")
                            monitor_after_user_input(fp, baseline_hash=baseline[fp]["hash"])
                            changes_in_scan += 1
                            break
                        elif choice == "r":
                            append_audit_log(fp, old_hash, current_hash, action="user_rejected")
                            print(f"  [已拒绝] {fp} 的变更未被采纳，基准保持不变。")
                            monitor_after_user_input(fp, baseline_hash=baseline[fp]["hash"])
                            break
                        elif choice == "d":
                            show_diff(fp)
                        else:
                            print("  无效选择，请输入 a / r / d")

            # 检测已删除的文件
            watched_baselines = [k for k in baseline if k.startswith(watch_dir)]
            for fp in watched_baselines:
                if fp not in current_files:
                    old_hash = baseline[fp]["hash"]
                    # 清理快照
                    sp = _snapshot_path(fp)
                    if os.path.exists(sp):
                        os.remove(sp)
                    wsp = _watch_snapshot_path(fp)
                    if os.path.exists(wsp):
                        os.remove(wsp)
                    del baseline[fp]
                    entry = append_audit_log(fp, old_hash, "N/A", action="deleted")
                    ts = entry["timestamp"]
                    print(f"  [{ts}] 文件已删除: {fp}")
                    changes_in_scan += 1

            if changes_in_scan > 0:
                save_baseline(baseline)
                print(f"  >> 扫描 #{scan_count} 完成，处理了 {changes_in_scan} 项变更\n")

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\n  目录监控已停止。共完成 {scan_count} 次扫描。")


# ============================================================
# 辅助功能：初始化基准哈希
# ============================================================
def init_baseline(paths):
    """为指定的文件或目录初始化基准哈希。"""
    baseline = load_baseline()
    count = 0

    for p in paths:
        p = os.path.normpath(p)
        if os.path.isfile(p):
            h = calculate_hash(p)
            baseline[p] = {"hash": h, "registered_at": datetime.now().isoformat()}
            save_snapshot(p)
            append_audit_log(p, "N/A", h, action="init")
            print(f"  已注册: {p}  ->  {h[:16]}... (快照已保存)")
            count += 1
        elif os.path.isdir(p):
            for root, _, files in os.walk(p):
                for name in files:
                    fp = os.path.normpath(os.path.join(root, name))
                    h = calculate_hash(fp)
                    if h:
                        baseline[fp] = {"hash": h, "registered_at": datetime.now().isoformat()}
                        save_snapshot(fp)
                        append_audit_log(fp, "N/A", h, action="init")
                        print(f"  已注册: {fp}  ->  {h[:16]}... (快照已保存)")
                        count += 1

    save_baseline(baseline)
    print(f"\n  共注册 {count} 个文件的基准哈希。")


# ============================================================
# 辅助功能：显示状态
# ============================================================
def show_status():
    """显示所有受监控文件的当前完整性状态。"""
    baseline = load_baseline()
    if not baseline:
        print("  尚未注册任何基准哈希。请先运行 init 命令。")
        return

    print(f"\n{'='*72}")
    print(f"  文件完整性状态报告")
    print(f"  时间: {datetime.now().isoformat()}")
    print(f"{'='*72}")
    print(f"  {'状态':<6} {'文件路径':<40} {'哈希值 (前16位)'}")
    print(f"  {'-'*6} {'-'*40} {'-'*16}")

    ok_count = 0
    warn_count = 0
    miss_count = 0

    for fp, info in sorted(baseline.items()):
        stored_hash = info["hash"]
        current_hash = calculate_hash(fp)
        if current_hash is None:
            status = "缺失"
            hash_display = "文件不存在"
            miss_count += 1
        elif current_hash == stored_hash:
            status = "正常"
            hash_display = current_hash[:16] + "..."
            ok_count += 1
        else:
            status = "异常!"
            hash_display = current_hash[:16] + "... != " + stored_hash[:16] + "..."
            warn_count += 1

        protected = " [受保护]" if fp in PROTECTED_FILES else ""
        print(f"  {status:<6} {fp:<40} {hash_display}{protected}")

    print(f"\n  合计: {ok_count} 正常 / {warn_count} 异常 / {miss_count} 缺失")

    # 审计日志统计
    if os.path.exists(AUDIT_LOG_FILE):
        with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
            log_count = sum(1 for _ in f)
        print(f"  审计日志: {AUDIT_LOG_FILE} ({log_count} 条记录)")
    print()


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="文件完整性监控脚本 — 防止智能体篡改关键文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python file_integrity_monitor.py init MEMORY.md dev_portal/
  python file_integrity_monitor.py check MEMORY.md
  python file_integrity_monitor.py watch dev_portal --interval 3
  python file_integrity_monitor.py status
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # init
    p_init = subparsers.add_parser("init", help="初始化基准哈希")
    p_init.add_argument("paths", nargs="+", help="文件或目录路径")

    # check
    p_check = subparsers.add_parser("check", help="校验文件完整性（Agent 调用前拦截）")
    p_check.add_argument("file", help="要校验的文件路径")
    p_check.add_argument("--no-interactive", action="store_true", help="非交互模式，自动拒绝异常")

    # watch
    p_watch = subparsers.add_parser("watch", help="持续监控目录变更")
    p_watch.add_argument("directory", nargs="?", default=DEFAULT_WATCH_DIR, help="监控目录")
    p_watch.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="扫描间隔（秒）")

    # status
    subparsers.add_parser("status", help="显示所有文件的完整性状态")

    args = parser.parse_args()

    if args.command == "init":
        init_baseline(args.paths)
    elif args.command == "check":
        result = check_file_integrity(args.file, interactive=not args.no_interactive)
        sys.exit(0 if result else 1)
    elif args.command == "watch":
        watch_directory(args.directory, args.interval)
    elif args.command == "status":
        show_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
