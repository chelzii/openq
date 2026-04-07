"""
文件完整性监听脚本（基于 SHA-256）
================================
能力：
1. 对关键文件进行周期扫描（默认每 5 秒）并记录哈希变化。
2. 维护“可信基准哈希”（trusted）与“最近观测哈希”（observed）。
3. 在调用关键流程前，对 system_prompt.md 做拦截校验。
4. 发生不一致时，弹出确认窗口并展示 diff；拒绝则拦截调用。
5. 生成链式时间序列日志（每条日志含前一条事件哈希）。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class FileIntegrityMonitor:
    def __init__(
        self,
        workspace_dir: Path,
        system_prompt_path: Path,
        watch_targets: Optional[List[Path]] = None,
        scan_interval_seconds: int = 5,
    ):
        self.workspace_dir = workspace_dir
        self.system_prompt_path = system_prompt_path
        self.scan_interval_seconds = scan_interval_seconds

        self.state_file = self.workspace_dir / ".integrity_state.json"
        self.log_file = self.workspace_dir / ".integrity_audit.log"

        self.watch_targets = self._normalize_targets(watch_targets or [])
        if self.system_prompt_path not in self.watch_targets:
            self.watch_targets.append(self.system_prompt_path)

        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.state = self._load_or_init_state()

    def _normalize_targets(self, targets: List[Path]) -> List[Path]:
        deduped: List[Path] = []
        seen = set()
        for path in targets:
            key = str(path.resolve())
            if key not in seen:
                seen.add(key)
                deduped.append(path)
        return deduped

    def _collect_current_files(self) -> List[Path]:
        files: List[Path] = []
        for target in self.watch_targets:
            # 动态判断目标类型，支持后续才创建出来的目录。
            if target.is_dir():
                for root, _, names in os.walk(target):
                    for name in names:
                        files.append(Path(root) / name)
            else:
                files.append(target)

        # 去重并保持顺序
        deduped: List[Path] = []
        seen = set()
        for path in files:
            key = str(path.resolve())
            if key not in seen:
                seen.add(key)
                deduped.append(path)
        return deduped

    def _safe_read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _sha256_file(self, path: Path) -> Optional[str]:
        if not path.exists() or not path.is_file():
            return None
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace_dir.resolve())).replace("\\", "/")
        except Exception:
            return str(path.resolve()).replace("\\", "/")

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _load_or_init_state(self) -> Dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        trusted = {}
        observed = {}
        snapshots = {}

        for path in self._collect_current_files():
            file_hash = self._sha256_file(path)
            rel = self._relative(path)
            observed[rel] = file_hash
            trusted[rel] = file_hash
            if rel == self._relative(self.system_prompt_path):
                snapshots[rel] = self._safe_read_text(path)

        state = {
            "created_at": self._now(),
            "scan_interval_seconds": self.scan_interval_seconds,
            "trusted": trusted,
            "observed": observed,
            "snapshots": snapshots,
            "last_event_hash": "GENESIS",
        }
        self._save_state(state)
        return state

    def _save_state(self, state: Optional[Dict] = None) -> None:
        content = state if state is not None else self.state
        self.state_file.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_event_chain_hash(self, prev_event_hash: str, payload: Dict) -> str:
        digest = hashlib.sha256()
        digest.update(prev_event_hash.encode("utf-8"))
        digest.update(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        return digest.hexdigest()

    def _append_log(self, event_type: str, path: Path, old_hash: Optional[str], new_hash: Optional[str], detail: str) -> None:
        rel = self._relative(path)
        payload = {
            "time": self._now(),
            "event_type": event_type,
            "path": rel,
            "old_hash": old_hash,
            "new_hash": new_hash,
            "detail": detail,
        }
        prev = self.state.get("last_event_hash", "GENESIS")
        event_hash = self._build_event_chain_hash(prev, payload)
        payload["prev_event_hash"] = prev
        payload["event_hash"] = event_hash

        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

        self.state["last_event_hash"] = event_hash

    def _scan_once(self) -> None:
        with self._state_lock:
            observed = self.state.setdefault("observed", {})
            for path in self._collect_current_files():
                rel = self._relative(path)
                new_hash = self._sha256_file(path)
                old_hash = observed.get(rel)
                if new_hash != old_hash:
                    self._append_log(
                        event_type="FILE_HASH_CHANGED",
                        path=path,
                        old_hash=old_hash,
                        new_hash=new_hash,
                        detail="周期扫描检测到文件哈希变化",
                    )
                    observed[rel] = new_hash
                    print(
                        f"[监听] {self._now()} 检测到变更: {rel} | "
                        f"old={old_hash or 'NONE'} -> new={new_hash or 'NONE'}"
                    )
            self._save_state()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()

        def _runner() -> None:
            while not self._stop_event.is_set():
                try:
                    self._scan_once()
                except Exception as exc:
                    print(f"[监听] 扫描异常: {exc}")
                self._stop_event.wait(self.scan_interval_seconds)

        self._thread = threading.Thread(target=_runner, daemon=True, name="FileIntegrityMonitor")
        self._thread.start()
        print(f"[监听] 完整性监听已启动，扫描周期 {self.scan_interval_seconds} 秒")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _diff_for_system_prompt(self, trusted_text: str, current_text: str, max_lines: int = 120) -> str:
        diff = list(
            difflib.unified_diff(
                trusted_text.splitlines(),
                current_text.splitlines(),
                fromfile="trusted/system_prompt.md",
                tofile="current/system_prompt.md",
                lineterm="",
            )
        )
        if not diff:
            return "(无可展示差异，但哈希不一致，可能是编码或换行差异)"
        if len(diff) > max_lines:
            return "\n".join(diff[:max_lines] + ["... (差异过长，已截断)"])
        return "\n".join(diff)

    def _ask_user_confirmation(self, diff_text: str) -> bool:
        # 优先 GUI 弹框；失败时回退到终端确认。
        try:
            import tkinter as tk
            from tkinter import scrolledtext

            result = {"value": False}

            root = tk.Tk()
            root.title("system_prompt.md 完整性告警")
            root.geometry("900x620")

            header = tk.Label(
                root,
                text="检测到 system_prompt.md 与基准哈希不一致，请人工确认是否放行本次调用",
                font=("Microsoft YaHei UI", 12, "bold"),
                wraplength=860,
                justify="left",
            )
            header.pack(padx=12, pady=(12, 8), anchor="w")

            desc = tk.Label(
                root,
                text="以下为基准版本与当前版本差异（unified diff）：",
                font=("Microsoft YaHei UI", 10),
                justify="left",
            )
            desc.pack(padx=12, pady=(0, 8), anchor="w")

            text_area = scrolledtext.ScrolledText(root, wrap="none", font=("Consolas", 10))
            text_area.insert("1.0", diff_text)
            text_area.configure(state="disabled")
            text_area.pack(fill="both", expand=True, padx=12, pady=8)

            btn_frame = tk.Frame(root)
            btn_frame.pack(fill="x", padx=12, pady=(6, 12))

            def on_yes() -> None:
                result["value"] = True
                root.destroy()

            def on_no() -> None:
                result["value"] = False
                root.destroy()

            yes_btn = tk.Button(btn_frame, text="是（放行并更新基准）", width=20, command=on_yes)
            no_btn = tk.Button(btn_frame, text="否（拦截）", width=12, command=on_no)
            yes_btn.pack(side="left")
            no_btn.pack(side="left", padx=10)

            root.mainloop()
            return bool(result["value"])
        except Exception:
            print("[告警] system_prompt.md 与基准哈希不一致。")
            print("[告警] 以下为主要差异：")
            print(diff_text)
            answer = input("是否放行本次调用并更新基准哈希？(y/N): ").strip().lower()
            return answer in ("y", "yes")

    def verify_system_prompt_before_call(self) -> bool:
        """
        在每次 Agent 调用前执行。
        True: 放行
        False: 拦截
        """
        with self._state_lock:
            rel = self._relative(self.system_prompt_path)
            trusted = self.state.setdefault("trusted", {})
            snapshots = self.state.setdefault("snapshots", {})
            observed = self.state.setdefault("observed", {})

            trusted_hash = trusted.get(rel)
            current_hash = self._sha256_file(self.system_prompt_path)

            # 正常：哈希一致
            if trusted_hash == current_hash:
                self._append_log(
                    event_type="PRE_CALL_VERIFIED",
                    path=self.system_prompt_path,
                    old_hash=trusted_hash,
                    new_hash=current_hash,
                    detail="调用前校验通过",
                )
                self._save_state()
                return True

            # 异常：哈希不一致，生成差异并弹框确认
            trusted_text = snapshots.get(rel, "")
            current_text = self._safe_read_text(self.system_prompt_path)
            diff_text = self._diff_for_system_prompt(trusted_text, current_text)

            self._append_log(
                event_type="PRE_CALL_MISMATCH",
                path=self.system_prompt_path,
                old_hash=trusted_hash,
                new_hash=current_hash,
                detail="调用前校验发现与基准不一致，等待人工确认",
            )
            self._save_state()

        allow = self._ask_user_confirmation(diff_text)

        with self._state_lock:
            rel = self._relative(self.system_prompt_path)
            trusted = self.state.setdefault("trusted", {})
            snapshots = self.state.setdefault("snapshots", {})
            observed = self.state.setdefault("observed", {})
            trusted_hash = trusted.get(rel)
            current_hash = self._sha256_file(self.system_prompt_path)

            if allow:
                # 用户确认放行：更新基准哈希与快照
                trusted[rel] = current_hash
                observed[rel] = current_hash
                snapshots[rel] = self._safe_read_text(self.system_prompt_path)
                self._append_log(
                    event_type="USER_CONFIRMED_AND_ALLOWED",
                    path=self.system_prompt_path,
                    old_hash=trusted_hash,
                    new_hash=current_hash,
                    detail="用户确认修改合理，放行并更新基准",
                )
                self._save_state()
                print("[防御] 用户已确认，本次调用放行，并更新 system_prompt.md 基准哈希。")
                return True

            # 用户拒绝：仅提示“存在修改迹象”，避免直接定性已被修改
            self._append_log(
                event_type="USER_REJECTED_AND_BLOCKED",
                path=self.system_prompt_path,
                old_hash=trusted_hash,
                new_hash=current_hash,
                detail="用户拒绝放行。仅记录为存在修改迹象并拦截调用",
            )
            self._save_state()
            print("[防御] 检测到 system_prompt.md 存在修改迹象，用户未确认，已拦截本次调用。")
            return False


def build_default_watch_targets(workspace_dir: Path) -> List[Path]:
    """构建默认监听目标：openclaw.json、markdown 目录以及 system_prompt.md 所在目录。"""
    return [
        workspace_dir / "openclaw.json",
        workspace_dir / "markdown",
        workspace_dir / "docs" / "markdown",
    ]
