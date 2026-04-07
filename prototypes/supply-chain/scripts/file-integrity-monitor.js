const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const readline = require('readline');
const { execFileSync } = require('child_process');

const ROOT_DIR = process.cwd();
const INTEGRITY_DIR = path.resolve(ROOT_DIR, '.integrity');
const BASELINE_FILE = path.resolve(INTEGRITY_DIR, 'hash-baseline.json');
const AUDIT_LOG_FILE = path.resolve(INTEGRITY_DIR, 'integrity-audit.jsonl');
const SNAPSHOT_DIR = path.resolve(INTEGRITY_DIR, 'snapshots');

const DEFAULT_INTERVAL_MS = 5000;
const WATCH_EXTENSIONS = new Set(['.md', '.markdown', '.json', '.yaml', '.yml', '.js']);
const WATCH_IGNORED_DIRS = new Set(['node_modules', '.git', 'dist', '.integrity']);
const PROTECTED_FILES = ['index.js', 'openclaw.json'];
let integrityInitLogged = false;

function ensureIntegrityDirs() {
    const integrityDirExisted = fs.existsSync(INTEGRITY_DIR);
    const snapshotDirExisted = fs.existsSync(SNAPSHOT_DIR);

    if (!integrityDirExisted) {
        fs.mkdirSync(INTEGRITY_DIR, { recursive: true });
    }
    if (!snapshotDirExisted) {
        fs.mkdirSync(SNAPSHOT_DIR, { recursive: true });
    }

    if (!integrityInitLogged) {
        const integrityState = integrityDirExisted ? '已存在' : '已创建';
        const snapshotState = snapshotDirExisted ? '已存在' : '已创建';
        console.log(`[monitor] .integrity 存储路径: ${INTEGRITY_DIR} (${integrityState})`);
        console.log(`[monitor] 快照路径: ${SNAPSHOT_DIR} (${snapshotState})`);
        integrityInitLogged = true;
    }
}

function normalizeToRepoPath(inputPath) {
    const absolute = path.resolve(ROOT_DIR, inputPath);
    return path.relative(ROOT_DIR, absolute).replace(/\\/g, '/');
}

function toAbsolute(repoPath) {
    return path.resolve(ROOT_DIR, repoPath);
}

function hashFile(absolutePath) {
    if (!fs.existsSync(absolutePath) || !fs.statSync(absolutePath).isFile()) {
        return null;
    }
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(absolutePath);

    return new Promise((resolve, reject) => {
        stream.on('data', (chunk) => hash.update(chunk));
        stream.on('error', reject);
        stream.on('end', () => resolve(hash.digest('hex')));
    });
}

function loadBaseline() {
    if (!fs.existsSync(BASELINE_FILE)) {
        return {};
    }
    try {
        return JSON.parse(fs.readFileSync(BASELINE_FILE, 'utf8'));
    } catch {
        return {};
    }
}

function saveBaseline(data) {
    ensureIntegrityDirs();
    fs.writeFileSync(BASELINE_FILE, JSON.stringify(data, null, 2), 'utf8');
}

function readAuditTailHash() {
    if (!fs.existsSync(AUDIT_LOG_FILE)) {
        return '0'.repeat(64);
    }

    const lines = fs.readFileSync(AUDIT_LOG_FILE, 'utf8').trim().split(/\r?\n/);
    if (!lines.length || !lines[0]) {
        return '0'.repeat(64);
    }

    try {
        const last = JSON.parse(lines[lines.length - 1]);
        return last.entry_hash || '0'.repeat(64);
    } catch {
        return '0'.repeat(64);
    }
}

function appendAuditLog({ file, action, oldHash, newHash, note }) {
    ensureIntegrityDirs();
    const prevEntryHash = readAuditTailHash();
    const entry = {
        timestamp: new Date().toISOString(),
        file,
        action,
        old_hash: oldHash,
        new_hash: newHash,
        note: note || '',
        prev_entry_hash: prevEntryHash,
    };

    const raw = JSON.stringify(entry, Object.keys(entry).sort());
    entry.entry_hash = crypto.createHash('sha256').update(raw, 'utf8').digest('hex');

    fs.appendFileSync(AUDIT_LOG_FILE, `${JSON.stringify(entry)}\n`, 'utf8');
}

function snapshotPathFor(repoPath) {
    const safe = repoPath.replace(/[/:\\]/g, '__');
    return path.resolve(SNAPSHOT_DIR, safe);
}

function saveSnapshot(repoPath) {
    const absolute = toAbsolute(repoPath);
    if (!fs.existsSync(absolute)) {
        return;
    }
    ensureIntegrityDirs();
    fs.copyFileSync(absolute, snapshotPathFor(repoPath));
}

function loadSnapshotLines(repoPath) {
    const sp = snapshotPathFor(repoPath);
    if (!fs.existsSync(sp)) {
        return [];
    }
    return fs.readFileSync(sp, 'utf8').split(/\r?\n/);
}

function loadCurrentLines(repoPath) {
    const absolute = toAbsolute(repoPath);
    if (!fs.existsSync(absolute)) {
        return [];
    }
    return fs.readFileSync(absolute, 'utf8').split(/\r?\n/);
}

function buildLineDiff(repoPath, maxLines = 60) {
    const oldLines = loadSnapshotLines(repoPath);
    const newLines = loadCurrentLines(repoPath);

    if (!oldLines.length) {
        return [`[${repoPath}] 没有历史快照，当前内容将作为参考。`];
    }

    const output = [];
    const max = Math.max(oldLines.length, newLines.length);

    for (let i = 0; i < max && output.length < maxLines; i += 1) {
        const oldLine = oldLines[i];
        const newLine = newLines[i];

        if (oldLine === newLine) {
            continue;
        }

        if (oldLine !== undefined) {
            output.push(`- L${i + 1}: ${oldLine}`);
        }
        if (newLine !== undefined) {
            output.push(`+ L${i + 1}: ${newLine}`);
        }
    }

    if (!output.length) {
        return ['(未发现文本差异)'];
    }

    if (output.length >= maxLines) {
        output.push('...差异内容已截断，请查看完整文件进行审计。');
    }

    return output;
}

function askInTerminal(question) {
    return new Promise((resolve) => {
        const rl = readline.createInterface({
            input: process.stdin,
            output: process.stdout,
        });
        rl.question(question, (answer) => {
            rl.close();
            resolve(answer.trim());
        });
    });
}

function escapeForPowerShellText(text) {
    return text.replace(/`/g, '``').replace(/"/g, '""');
}

function askViaWindowsMessageBox(title, message) {
    try {
        const script = [
            'Add-Type -AssemblyName System.Windows.Forms;',
            `$title = "${escapeForPowerShellText(title)}";`,
            `$message = "${escapeForPowerShellText(message)}";`,
            '$result = [System.Windows.Forms.MessageBox]::Show($message, $title, [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Warning);',
            'if ($result -eq [System.Windows.Forms.DialogResult]::Yes) { Write-Output "YES" } else { Write-Output "NO" }',
        ].join(' ');

        const output = execFileSync('powershell', ['-NoProfile', '-Command', script], {
            encoding: 'utf8',
            stdio: ['ignore', 'pipe', 'ignore'],
        }).trim();

        return output === 'YES';
    } catch {
        return null;
    }
}

function askViaZenity(title, message) {
    try {
        execFileSync('zenity', [
            '--question',
            '--title',
            title,
            '--width',
            '780',
            '--height',
            '520',
            '--text',
            message,
        ], {
            stdio: ['ignore', 'ignore', 'ignore'],
        });
        return true;
    } catch (error) {
        // zenity 在用户选择“否”时会返回非 0；仅在可确认的情况下返回 false。
        if (error && typeof error.status === 'number') {
            if (error.status === 1) {
                return false;
            }
        }

        const noDisplay = !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY;
        if (noDisplay) {
            console.log('[monitor] Linux 弹窗不可用: 未检测到 DISPLAY/WAYLAND_DISPLAY，已降级为终端确认。');
        } else {
            console.log('[monitor] Linux 弹窗不可用: 未找到或无法执行 zenity，已降级为终端确认。');
        }
        return null;
    }
}

async function confirmSuspiciousChange(repoPath, diffLines) {
    const title = 'OpenClaw 完整性告警';
    const shortDiff = diffLines.slice(0, 12).join('\n');
    const message = [
        `检测到 ${repoPath} 出现哈希异常。`,
        '',
        '是否接受本次变更并更新基线？',
        '',
        '差异预览：',
        shortDiff || '(无)',
    ].join('\n');

    // 无论是否弹窗，都先在运行界面打印变更，方便 OpenClaw 审计显示。
    console.log('\n[完整性告警] 检测到文件差异（运行界面审计输出）:');
    console.log(diffLines.join('\n'));

    let dialogResult = null;
    if (process.platform === 'win32') {
        dialogResult = askViaWindowsMessageBox(title, message);
    } else if (process.platform === 'linux') {
        dialogResult = askViaZenity(title, message);
    }

    if (dialogResult !== null) {
        return dialogResult;
    }

    console.log('[monitor] 当前使用终端交互确认（y/N）。');
    const answer = await askInTerminal('接受本次变更并更新基线吗？(y/N): ');
    return answer.toLowerCase() === 'y' || answer.toLowerCase() === 'yes';
}

async function registerBaselineForFile(filePath) {
    const repoPath = normalizeToRepoPath(filePath);
    const absolute = toAbsolute(repoPath);

    if (!fs.existsSync(absolute)) {
        return { ok: false, reason: 'missing', file: repoPath };
    }

    const baseline = loadBaseline();
    const currentHash = await hashFile(absolute);

    baseline[repoPath] = {
        hash: currentHash,
        registered_at: new Date().toISOString(),
        source: 'init',
    };

    saveBaseline(baseline);
    saveSnapshot(repoPath);
    appendAuditLog({
        file: repoPath,
        action: 'baseline_registered',
        oldHash: 'N/A',
        newHash: currentHash,
        note: 'Registered baseline for integrity monitor',
    });

    return { ok: true, file: repoPath, hash: currentHash };
}

async function ensureBaselineForFile(filePath) {
    const repoPath = normalizeToRepoPath(filePath);
    const absolute = toAbsolute(repoPath);

    if (!fs.existsSync(absolute)) {
        return { ok: false, reason: 'missing', file: repoPath };
    }

    const baseline = loadBaseline();
    if (baseline[repoPath]) {
        return { ok: true, file: repoPath, hash: baseline[repoPath].hash, existing: true };
    }

    return registerBaselineForFile(filePath);
}

async function checkFileIntegrity(filePath, options = {}) {
    const interactive = options.interactive !== false;
    const reason = options.reason || 'agent_call';

    const repoPath = normalizeToRepoPath(filePath);
    const absolute = toAbsolute(repoPath);

    if (!fs.existsSync(absolute)) {
        return { ok: false, blocked: true, reason: 'missing', file: repoPath };
    }

    const baseline = loadBaseline();
    const currentHash = await hashFile(absolute);

    if (!baseline[repoPath]) {
        baseline[repoPath] = {
            hash: currentHash,
            registered_at: new Date().toISOString(),
            source: `auto:${reason}`,
        };
        saveBaseline(baseline);
        saveSnapshot(repoPath);
        appendAuditLog({
            file: repoPath,
            action: 'baseline_auto_registered',
            oldHash: 'N/A',
            newHash: currentHash,
            note: `Auto registration during ${reason}`,
        });
        return { ok: true, initialized: true, file: repoPath };
    }

    const baselineHash = baseline[repoPath].hash;
    if (baselineHash === currentHash) {
        return { ok: true, file: repoPath, hash: currentHash };
    }

    const diffLines = buildLineDiff(repoPath);
    appendAuditLog({
        file: repoPath,
        action: 'suspicious_trace',
        oldHash: baselineHash,
        newHash: currentHash,
        note: `Mismatch before ${reason}`,
    });

    if (!interactive) {
        return {
            ok: false,
            blocked: true,
            file: repoPath,
            oldHash: baselineHash,
            newHash: currentHash,
            diffLines,
            message: '检测到修改迹象，非交互模式已拦截。',
        };
    }

    const accepted = await confirmSuspiciousChange(repoPath, diffLines);
    if (!accepted) {
        appendAuditLog({
            file: repoPath,
            action: 'user_rejected_trace',
            oldHash: baselineHash,
            newHash: currentHash,
            note: 'User rejected suspicious change trace',
        });

        return {
            ok: false,
            blocked: true,
            file: repoPath,
            oldHash: baselineHash,
            newHash: currentHash,
            diffLines,
            message: '检测到修改迹象，用户未确认，调用已拦截。',
        };
    }

    baseline[repoPath] = {
        hash: currentHash,
        registered_at: new Date().toISOString(),
        source: `accepted:${reason}`,
        accepted_from: baselineHash,
    };
    saveBaseline(baseline);
    saveSnapshot(repoPath);
    appendAuditLog({
        file: repoPath,
        action: 'user_accepted_change',
        oldHash: baselineHash,
        newHash: currentHash,
        note: 'User accepted and baseline updated',
    });

    return {
        ok: true,
        accepted: true,
        file: repoPath,
        oldHash: baselineHash,
        newHash: currentHash,
        diffLines,
    };
}

function shouldWatchFile(repoPath) {
    const ext = path.extname(repoPath).toLowerCase();
    if (WATCH_EXTENSIONS.has(ext)) {
        return true;
    }

    return PROTECTED_FILES.includes(repoPath);
}

function walkFiles(baseDir, callback) {
    const children = fs.readdirSync(baseDir, { withFileTypes: true });

    for (const child of children) {
        const absolute = path.resolve(baseDir, child.name);
        const repoPath = normalizeToRepoPath(absolute);

        if (child.isDirectory()) {
            if (WATCH_IGNORED_DIRS.has(child.name)) {
                continue;
            }
            walkFiles(absolute, callback);
            continue;
        }

        callback(repoPath, absolute);
    }
}

async function scanWatchTargets(watchDir) {
    const absoluteWatchDir = path.resolve(ROOT_DIR, watchDir || '.');
    const files = [];

    if (!fs.existsSync(absoluteWatchDir)) {
        return files;
    }

    walkFiles(absoluteWatchDir, (repoPath, absolute) => {
        if (shouldWatchFile(repoPath)) {
            files.push({ repoPath, absolute });
        }
    });

    return files;
}

function startWatch(options = {}) {
    const watchDir = options.watchDir || '.';
    const intervalMs = Number(options.intervalMs || DEFAULT_INTERVAL_MS);
    let timer = null;
    const pending = new Map();

    console.log(`[monitor] 目录监听启动: ${watchDir}, 间隔 ${Math.round(intervalMs / 1000)} 秒`);

    const scan = async () => {
        const baseline = loadBaseline();
        const files = await scanWatchTargets(watchDir);

        for (const file of files) {
            const currentHash = await hashFile(file.absolute);
            if (!currentHash) {
                continue;
            }

            const record = baseline[file.repoPath];
            if (!record) {
                continue;
            }

            if (record.hash === currentHash) {
                pending.delete(file.repoPath);
                continue;
            }

            const state = pending.get(file.repoPath);
            if (!state || state.hash !== currentHash) {
                pending.set(file.repoPath, {
                    hash: currentHash,
                    seen: 1,
                    firstSeen: new Date().toISOString(),
                });

                appendAuditLog({
                    file: file.repoPath,
                    action: 'watch_suspicious_trace',
                    oldHash: record.hash,
                    newHash: currentHash,
                    note: 'First mismatch seen by watch scanner',
                });

                console.log(`[monitor] 迹象: ${file.repoPath} 哈希异常，等待下一轮确认...`);
                continue;
            }

            const seen = state.seen + 1;
            pending.set(file.repoPath, { ...state, seen });

            if (seen < 2) {
                continue;
            }

            appendAuditLog({
                file: file.repoPath,
                action: 'watch_confirmed_change',
                oldHash: record.hash,
                newHash: currentHash,
                note: 'Mismatch persisted for two scans',
            });

            console.log(`[monitor] 已确认变更: ${file.repoPath}`);
            console.log(buildLineDiff(file.repoPath, 20).join('\n'));
        }
    };

    timer = setInterval(() => {
        scan().catch((err) => {
            console.error(`[monitor] 扫描失败: ${err.message}`);
        });
    }, intervalMs);

    scan().catch((err) => {
        console.error(`[monitor] 首次扫描失败: ${err.message}`);
    });

    return () => {
        if (timer) {
            clearInterval(timer);
            timer = null;
            console.log('[monitor] 目录监听已停止');
        }
    };
}

function startProtectedFileGuard(options = {}) {
    const files = (options.files || []).map((p) => normalizeToRepoPath(p));
    const intervalMs = Number(options.intervalMs || 1000);
    const interactive = options.interactive !== false;
    const onEvent = typeof options.onEvent === 'function' ? options.onEvent : () => {};
    const pending = new Set();

    if (!files.length) {
        return () => {};
    }

    let timer = null;
    let running = false;

    const scan = async () => {
        if (running) {
            return;
        }
        running = true;

        try {
            for (const repoPath of files) {
                if (pending.has(repoPath)) {
                    continue;
                }

                const result = await checkFileIntegrity(repoPath, {
                    interactive,
                    reason: 'deploy_runtime_guard',
                });

                if (result.ok) {
                    continue;
                }

                pending.add(repoPath);
                onEvent({
                    type: 'suspicious',
                    file: repoPath,
                    result,
                });
                pending.delete(repoPath);
            }
        } catch (error) {
            onEvent({
                type: 'error',
                error,
            });
        } finally {
            running = false;
        }
    };

    timer = setInterval(() => {
        scan().catch(() => {});
    }, intervalMs);

    scan().catch(() => {});

    return () => {
        if (timer) {
            clearInterval(timer);
            timer = null;
        }
    };
}

async function initCommand(paths) {
    const targets = paths.length ? paths : PROTECTED_FILES;

    for (const target of targets) {
        const result = await registerBaselineForFile(target);
        if (!result.ok) {
            console.log(`[init] 跳过 ${result.file}: 文件不存在`);
            continue;
        }
        console.log(`[init] 已注册 ${result.file}: ${result.hash.slice(0, 16)}...`);
    }
}

async function checkCommand(filePath) {
    if (!filePath) {
        throw new Error('check 模式需要提供文件路径');
    }

    const result = await checkFileIntegrity(filePath, {
        interactive: true,
        reason: 'manual_check',
    });

    if (result.ok) {
        console.log(`[check] 校验通过: ${result.file}`);
        return;
    }

    console.log(`[check] ${result.message || '检测到异常并已阻断。'}`);
    process.exitCode = 2;
}

function parseWatchArgs(argv) {
    const watchDir = argv[0] && !argv[0].startsWith('--') ? argv[0] : '.';
    const intervalFlag = argv.find((arg) => arg.startsWith('--interval='));
    const intervalSec = intervalFlag ? Number(intervalFlag.split('=')[1]) : 5;

    return {
        watchDir,
        intervalMs: Number.isFinite(intervalSec) ? intervalSec * 1000 : DEFAULT_INTERVAL_MS,
    };
}

async function main() {
    ensureIntegrityDirs();

    const [, , command, ...argv] = process.argv;

    if (!command || command === 'help') {
        console.log('用法:');
        console.log('  node scripts/file-integrity-monitor.js init [文件列表...]');
        console.log('  node scripts/file-integrity-monitor.js check <文件>');
        console.log('  node scripts/file-integrity-monitor.js watch [目录] [--interval=5]');
        return;
    }

    if (command === 'init') {
        await initCommand(argv);
        return;
    }

    if (command === 'check') {
        await checkCommand(argv[0]);
        return;
    }

    if (command === 'watch') {
        const options = parseWatchArgs(argv);
        startWatch(options);
        return;
    }

    throw new Error(`未知命令: ${command}`);
}

if (require.main === module) {
    main().catch((err) => {
        console.error(`[monitor] 运行失败: ${err.message}`);
        process.exit(1);
    });
}

module.exports = {
    checkFileIntegrity,
    ensureBaselineForFile,
    registerBaselineForFile,
    startWatch,
    startProtectedFileGuard,
    buildLineDiff,
};
