import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pageTitle, blockedFirst, newlyBlocked, notificationText, blockKey } from '../../static/attention.js';

const a = (id, status, questions) => ({ session_id: id, short_id: id, name: `n-${id}`, status, questions });

test('pageTitle shows the count only when blocked', () => {
  assert.equal(pageTitle('Viewer', 0), 'Viewer');
  assert.equal(pageTitle('Viewer', 2), '(2) 待处理 · Viewer');
});

test('blockedFirst is a stable partition', () => {
  const list = [a('1', 'idle'), a('2', 'blocked'), a('3', 'busy'), a('4', 'blocked')];
  assert.deepEqual(blockedFirst(list).map((x) => x.session_id), ['2', '4', '1', '3']);
  assert.deepEqual(blockedFirst([]), []);
});

test('newlyBlocked ignores first load and already blocked agents', () => {
  const list = [a('1', 'blocked'), a('2', 'blocked'), a('3', 'idle')];
  assert.deepEqual(newlyBlocked(null, list), []);
  assert.deepEqual(newlyBlocked(new Set(['1']), list).map((x) => x.session_id), ['2']);
  assert.deepEqual(newlyBlocked(new Set(['1', '2']), list), []);
});

test('notificationText uses the first question, with fallbacks', () => {
  const q = a('1', 'blocked', [{ question: 'Which one?' }]);
  assert.deepEqual(notificationText(q), { title: 'n-1 需要你处理', body: 'Which one?' });
  assert.equal(notificationText(a('1', 'blocked')).body, '等待你的选择或确认');
  assert.ok(notificationText(a('1', 'blocked', [{ question: 'x'.repeat(500) }])).body.length <= 140);
});

test('blockKey changes with the question', () => {
  assert.notEqual(blockKey(a('1', 'blocked', [{ question: 'A' }])), blockKey(a('1', 'blocked', [{ question: 'B' }])));
});
