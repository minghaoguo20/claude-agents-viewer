import { test } from 'node:test';
import assert from 'node:assert/strict';
import { agentHash, parseAgentParams, siblingTarget, siblingJump, branchLabel, hitLeaf } from '../../static/branch.js';

const branch = (index, count = 3) => ({
  index,
  count,
  siblings: Array.from({ length: count }, (_, i) => ({ uuid: `u${i}`, idx: 10 * (i + 1), leaf: `L${i}` })),
});

test('agentHash builds optional at and b', () => {
  assert.equal(agentHash('s 1'), '#/agent/s%201');
  assert.equal(agentHash('s', 0), '#/agent/s?at=0');
  assert.equal(agentHash('s', null, 'a/b'), '#/agent/s?b=a%2Fb');
  assert.equal(agentHash('s', 7, 'L1'), '#/agent/s?at=7&b=L1');
});

test('parseAgentParams reads at and b, ignores junk and subagents', () => {
  const p = (q, sub = false) => parseAgentParams(new URLSearchParams(q), sub);
  assert.deepEqual(p('at=5&b=L1'), { at: 5, b: 'L1' });
  assert.deepEqual(p('at=x&b='), { at: null, b: null });
  assert.deepEqual(p(''), { at: null, b: null });
  assert.deepEqual(p('at=5&b=L1', true), { at: null, b: null });
});

test('hash round trip', () => {
  const hash = agentHash('sid', 3, 'a&b=c');
  const params = new URLSearchParams(hash.slice(hash.indexOf('?') + 1));
  assert.deepEqual(parseAgentParams(params, false), { at: 3, b: 'a&b=c' });
});

test('siblingTarget steps and clamps', () => {
  assert.deepEqual(siblingTarget(branch(2), 1, null), { idx: 30, leaf: 'L2', index: 3 });
  assert.deepEqual(siblingTarget(branch(2), -1, null), { idx: 10, leaf: 'L0', index: 1 });
  assert.equal(siblingTarget(branch(1), -1), null);
  assert.equal(siblingTarget(branch(3), 1), null);
  assert.equal(siblingTarget(undefined, 1), null);
  assert.equal(siblingTarget(branch(1, 1), 1), null);
});

test('siblingTarget drops the leaf when it is the default one', () => {
  assert.deepEqual(siblingTarget(branch(2), 1, 'L2'), { idx: 30, leaf: null, index: 3 });
});

test('label and hitLeaf', () => {
  assert.equal(branchLabel(branch(2)), '2/3');
  assert.equal(hitLeaf({ on_default_path: false, leaf: 'L' }), 'L');
  assert.equal(hitLeaf({ on_default_path: true, leaf: 'L' }), null);
  assert.equal(hitLeaf({ leaf: 'L' }), null);
});

test('siblingJump drops the default leaf only', () => {
  const sib = { uuid: 'u1', idx: 20, leaf: 'L1' };
  assert.deepEqual(siblingJump(sib, 'L1'), { idx: 20, leaf: null });
  assert.deepEqual(siblingJump(sib, 'L2'), { idx: 20, leaf: 'L1' });
  assert.deepEqual(siblingJump(sib), { idx: 20, leaf: 'L1' });
});
