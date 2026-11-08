// Pure helpers for rewind branches: route hash building/parsing and sibling selection.
// No DOM access, so they can be unit-tested under node.

/** Hash for an agent view: "#/agent/<sid>?at=<idx>&b=<leaf>"; `at` and `b` (pinned leaf) are optional. */
export function agentHash(sid, at = null, b = null) {
  const params = [];
  if (at != null) params.push(`at=${at}`);
  if (b) params.push(`b=${encodeURIComponent(b)}`);
  return `#/agent/${encodeURIComponent(sid)}${params.length ? `?${params.join('&')}` : ''}`;
}

/**
 * Jump target and pinned leaf from URLSearchParams of an agent route.
 * Subagent views support neither; a non-numeric `at` or an empty `b` is ignored.
 */
export function parseAgentParams(params, isSubagent) {
  if (isSubagent) return { at: null, b: null };
  const at = params.get('at');
  return { at: /^\d+$/.test(at || '') ? Number(at) : null, b: params.get('b') || null };
}

/**
 * Sibling reached by stepping `step` (-1 / +1) from a message's `branch` info, clamped to the ends.
 * Returns {idx, leaf, index} (`leaf` is null when it equals `defaultLeaf`, i.e. follow the latest path),
 * or null when there is nothing to switch to.
 */
export function siblingTarget(branch, step, defaultLeaf = null) {
  const siblings = branch?.siblings;
  if (!Array.isArray(siblings) || siblings.length < 2) return null;
  const from = Math.min(Math.max(branch.index - 1, 0), siblings.length - 1);
  const to = Math.min(Math.max(from + step, 0), siblings.length - 1);
  if (to === from) return null;
  const { idx, leaf } = siblings[to];
  return { idx, leaf: leaf === defaultLeaf ? null : leaf, index: to + 1 };
}

/** Jump target for a specific sibling entry: {idx, leaf}; `leaf` is null when it equals `defaultLeaf`. */
export function siblingJump(sibling, defaultLeaf = null) {
  return { idx: sibling.idx, leaf: sibling.leaf === defaultLeaf ? null : sibling.leaf };
}

/** "2/3" label. */
export const branchLabel = (branch) => `${branch.index}/${branch.count}`;

/** Pinned leaf to use for a search hit: only hits in a rewound branch need one. */
export const hitLeaf = (hit) => (hit.on_default_path === false && hit.leaf ? hit.leaf : null);
