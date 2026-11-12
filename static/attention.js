// Pure helpers for "blocked" (waiting for the user) agents: title, ordering, transition detection, notification text.
// No DOM access, so they can be unit-tested under node.

export const isBlocked = (agent) => agent?.status === 'blocked';

/** Page title: "(N) 待处理 · base" while N agents are blocked, else the base title. */
export function pageTitle(base, count) {
  return count > 0 ? `(${count}) 待处理 · ${base}` : base;
}

/** Stable partition: blocked agents first, the relative order of both parts is kept. */
export function blockedFirst(agents) {
  return [...agents.filter(isBlocked), ...agents.filter((a) => !isBlocked(a))];
}

/** Text of the first pending question, or ''. */
export function firstQuestion(agent) {
  return String(agent?.questions?.[0]?.question || '').trim();
}

/** Identity of one block: a new question on the same agent counts as a new block. */
export const blockKey = (agent) => `${agent.session_id}|${firstQuestion(agent)}`;

/**
 * Agents that just entered the blocked state. `prev` is the Set of session ids blocked at the previous poll,
 * or null on the first load (nothing is reported then).
 */
export function newlyBlocked(prev, agents) {
  if (!prev) return [];
  return agents.filter((a) => isBlocked(a) && !prev.has(a.session_id));
}

/** Notification title and body for a blocked agent. */
export function notificationText(agent) {
  const body = firstQuestion(agent) || agent.detail || '等待你的选择或确认';
  return { title: `${agent.name || agent.short_id} 需要你处理`, body: body.length > 140 ? `${body.slice(0, 139)}…` : body };
}
