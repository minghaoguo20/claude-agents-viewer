// Pure helpers for the background-task block (no DOM).

export const STATUS_LABELS = { running: '运行中', done: '已完成', failed: '失败', stopped: '已停止', unknown: '状态未知' };
export const KIND_LABELS = { shell: 'Bash', monitor: 'Monitor' };

const ORDER = { running: 0, unknown: 0, failed: 1, stopped: 2, done: 3 };

/** Status text; a failed task shows its exit code when known. */
export function statusLabel(task) {
  const base = STATUS_LABELS[task.status] || STATUS_LABELS.unknown;
  return task.status === 'failed' && Number.isInteger(task.exit_code) ? `${base} (${task.exit_code})` : base;
}

/** Unfinished (running / unknown) first, then failed, stopped, done; start order is kept within a group. */
export function sortTasks(tasks) {
  const rank = (t) => ORDER[t.status] ?? 0;
  return tasks.map((t, i) => [t, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([t]) => t);
}

/** Summary line of the collapsed task card: total, plus how many are unfinished (running / unknown). */
export function tasksSummary(tasks) {
  const open = tasks.filter((t) => t.status === 'running' || t.status === 'unknown').length;
  return `后台任务 · ${tasks.length}${open ? ` · ${open} 未结束` : ''}`;
}
