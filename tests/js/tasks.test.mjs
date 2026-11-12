import { test } from 'node:test';
import assert from 'node:assert/strict';
import { statusLabel, sortTasks, tasksSummary } from '../../static/tasks.js';

test('statusLabel maps statuses and shows failed exit codes', () => {
  assert.equal(statusLabel({ status: 'done' }), '已完成');
  assert.equal(statusLabel({ status: 'failed', exit_code: 2 }), '失败 (2)');
  assert.equal(statusLabel({ status: 'failed' }), '失败');
  assert.equal(statusLabel({ status: 'weird' }), '状态未知');
});

test('sortTasks puts unfinished first and is stable', () => {
  const t = (id, status) => ({ id, status });
  const out = sortTasks([t(1, 'done'), t(2, 'unknown'), t(3, 'failed'), t(4, 'stopped'), t(5, 'unknown')]);
  assert.deepEqual(out.map((x) => x.id), [2, 5, 3, 4, 1]);
  assert.deepEqual(sortTasks([]), []);
});

test('tasksSummary shows the total and the unfinished count only when non-zero', () => {
  assert.equal(tasksSummary([{ status: 'done' }, { status: 'failed' }]), '后台任务 · 2');
  assert.equal(tasksSummary([{ status: 'done' }, { status: 'running' }, { status: 'unknown' }]), '后台任务 · 3 · 2 未结束');
});
