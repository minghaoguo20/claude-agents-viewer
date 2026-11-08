"""Rewind-branch helpers: transcripts are trees (uuid -> parentUuid). Pure functions."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Node:
    """One transcript record. `msg` is the global message idx (None for non-messages)."""
    parent: str | None
    msg: int | None
    human: bool = False


def latest_leaves(nodes: dict[str, Node]) -> dict[str, str]:
    """uuid -> latest (file order) message uuid inside its subtree, in one reverse pass."""
    seq = {u: i for i, u in enumerate(nodes)}
    best: dict[str, str] = {}
    for uuid in reversed(list(nodes)):
        node = nodes[uuid]
        cand = best.get(uuid) or (uuid if node.msg is not None else None)
        if cand is None:
            continue
        best[uuid] = cand
        parent = node.parent
        if parent in nodes and (parent not in best or seq[cand] > seq[best[parent]]):
            best[parent] = cand
    return best


def sibling_groups(nodes: dict[str, Node]) -> dict[str, list[str]]:
    """Map each human message node in a group of >=2 same-parent humans to that group."""
    by_parent: dict[str | None, list[str]] = {}
    for uuid, node in nodes.items():
        if node.human:
            by_parent.setdefault(node.parent, []).append(uuid)
    return {u: group for group in by_parent.values() if len(group) > 1 for u in group}


def path_for(nodes: dict[str, Node], leaf: str) -> list[int]:
    """Message idxs from the root down to `leaf`; stops at missing parents and cycles."""
    out: list[int] = []
    seen: set[str] = set()
    cur: str | None = leaf
    while cur is not None and cur in nodes and cur not in seen:
        seen.add(cur)
        node = nodes[cur]
        if node.msg is not None and (not out or out[-1] != node.msg):
            out.append(node.msg)
        cur = node.parent
    out.reverse()
    return out


class BranchIndex:
    """Derived per-transcript structures; rebuilt only after new lines were ingested."""

    def __init__(self, nodes: dict[str, Node]) -> None:
        self.nodes = nodes
        self.tree = any(n.parent is not None for n in nodes.values())
        self.default_leaf: str | None = next(
            (u for u in reversed(list(nodes)) if nodes[u].msg is not None), None)
        self.latest = latest_leaves(nodes)
        self.groups = sibling_groups(nodes) if self.tree else {}
        self._default_idx: frozenset[int] | None = None

    def path(self, leaf: str | None) -> list[int]:
        """Message idxs of the path ending at `leaf` (default leaf when None)."""
        leaf = leaf or self.default_leaf
        return [] if leaf is None else path_for(self.nodes, leaf)

    def default_idxs(self) -> frozenset[int]:
        if self._default_idx is None:
            self._default_idx = frozenset(self.path(None))
        return self._default_idx

    def siblings_for(self, uuid: str | None) -> list[dict[str, object]] | None:
        """Sibling entries (file order, including itself) or None if not a branch point."""
        group = self.groups.get(uuid) if uuid else None
        if not group:
            return None
        return [{"uuid": u, "idx": self.nodes[u].msg, "leaf": self.latest.get(u)}
                for u in group]

    def latest_leaf_under(self, uuid: str | None) -> str | None:
        return self.latest.get(uuid) if uuid else None
