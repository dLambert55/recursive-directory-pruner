"""Core implementation for recursive directory pruning.

A Pruner walks a tree bottom-up and removes directories that contain no
files and no non-empty subdirectories. It is pure and dependency-free.

Design decisions (stated plainly so the tests and the README agree):

* A directory counts as "empty" only when it has zero files AND every
  child directory is itself empty (and therefore removed). Symlinks are
  treated as leaf entries: we never traverse through them, but a symlink
  target counts as content and keeps its parent directory alive. This is
  the safest choice on Windows, where symlinks are unreliable.
* remove_root=True lets the top-level directory itself be removed if it
  becomes empty. The default (False) leaves the root standing so repeated
  pruning runs are idempotent against an empty starting tree.
* max_depth is a first-class limit: N means at most N directory levels are
  descended into below the root. max_depth=0 inspects only the root's
  immediate children and never removes the root (the root is the reference
  frame, not a candidate at depth 0). This keeps the two limits
  orthogonal and avoids a class of off-by-one errors.
* Permission errors on directories we wanted to prune are tolerated: we
  leave the directory in place and record it in protected. A missing
  directory between our walk and our rmdir is treated the same way
  (someone else raced us) and silently ignored.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field


def _is_symlink(path: str) -> bool:
    """Return True if *path* is a symbolic link.

    We use os.lstat so the link itself is inspected, never its target.
    A symlink to a directory is NOT traversed by this Pruner; it is treated
    as a leaf entry. This avoids loops on pathological trees and is the only
    sensible behaviour on Windows where symlinks may be privileged-only.
    """
    return stat.S_ISLNK(os.lstat(path).st_mode)


def _list_dir(path: str) -> list[str]:
    """List *path* returning full child paths.

    os.listdir is non-recursive; the Pruner walks the tree explicitly so it
    can apply depth limits precisely.
    """
    return [os.path.join(path, name) for name in os.listdir(path)]


@dataclass
class PruneResult:
    """Outcome of a single prune() run.

    removed    -- directories successfully rmdir'd, in the order removed
    kept       -- directories that still contain files or non-empty children
    protected  -- directories we tried to remove but could not (EACCES, etc.)
    root_kept  -- True if the root directory still exists on disk afterwards
    """

    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)
    root_kept: bool = True


class Pruner:
    """Removes empty directories from a tree, bottom-up.

    Parameters
    ----------
    max_depth:
        Maximum directory levels to descend below the root. 0 means inspect
        only the root's immediate children. None means unlimited.
    stop_at:
        Absolute path prefix. Directories at or beneath this prefix are
        never removed. Used to pin a subtree boundary regardless of depth.
    remove_root:
        If True, the root directory itself may be removed when it becomes
        empty. Default False, which keeps the root standing.
    """

    def __init__(
        self,
        *,
        max_depth: int | None = None,
        stop_at: str | None = None,
        remove_root: bool = False,
    ) -> None:
        if max_depth is not None and max_depth < 0:
            raise ValueError("max_depth must be >= 0 or None")
        self.max_depth = max_depth
        self.stop_at = os.path.abspath(stop_at) if stop_at else None
        self.remove_root = remove_root

    def prune(self, root: str) -> PruneResult:
        """Prune empty directories beneath *root*.

        Returns a PruneResult describing the run. The root directory is
        never removed unless remove_root=True was passed to the constructor
        AND the root became empty.
        """
        root = os.path.abspath(root)
        result = PruneResult()
        # A missing root is a no-op: there is nothing to prune. Callers
        # sometimes feed in a path that was removed by an earlier run.
        if not os.path.isdir(root) or _is_symlink(root):
            result.root_kept = False
            return result
        self._prune_dir(root, 0, result)
        result.root_kept = os.path.isdir(root)
        return result

    def _prune_dir(
        self, current: str, depth: int, result: PruneResult
    ) -> bool:
        """Prune *current* recursively.

        Returns True if *current* is now empty (and was removed or eligible
        for removal by the caller), False if it still has live content.
        """
        # A directory beneath the stop_at prefix is never removed.
        if self.stop_at and (
            current == self.stop_at or current.startswith(
                self.stop_at + os.sep
            )
        ):
            result.kept.append(current)
            return False

        # max_depth counts levels descended below the root. depth=0 is the
        # root's own directory; depth=N is N levels down. When we are ABOUT
        # to descend past max_depth, we stop and treat the directory as
        # content (non-removable) so its parent stays alive.
        can_descend = self.max_depth is None or depth < self.max_depth

        # Children whose presence means *current* is non-empty and so must
        # be kept. We still recurse into subdirectories first so deeper
        # empty dirs get removed even when an ancestor must stay.
        non_empty_children: list[str] = []

        if can_descend:
            for child in _list_dir(current):
                try:
                    child_stat = os.lstat(child)
                except FileNotFoundError:
                    # Raced between listdir and lstat: treat as already gone.
                    continue
                if stat.S_ISDIR(child_stat.st_mode) and not stat.S_ISLNK(
                    child_stat.st_mode
                ):
                    child_is_empty = self._prune_dir(child, depth + 1, result)
                    if not child_is_empty:
                        non_empty_children.append(child)
                else:
                    # File or symlink: counts as content. We never follow
                    # symlinks, so a symlink-to-dir is treated as a leaf.
                    non_empty_children.append(child)
        else:
            # Cannot descend: any child at all counts as content.
            non_empty_children = _list_dir(current)

        if non_empty_children:
            result.kept.append(current)
            return False

        # current is empty of files AND its subdirs were all removed.
        # If we couldn't descend, we must NOT remove it: we only know it had
        # no children we could see, not that it is truly empty.
        if not can_descend:
            result.kept.append(current)
            return False

        # The root is removed only if the caller opted in.
        is_root = depth == 0
        if is_root and not self.remove_root:
            result.kept.append(current)
            return True

        try:
            os.rmdir(current)
            result.removed.append(current)
            return True
        except FileNotFoundError:
            # Raced: directory is gone. Treat as removed for the caller's
            # accounting of emptiness, but do not claim we removed it.
            return True
        except OSError:
            # EACCES, ENOTEMPTY (race added content), or similar. We cannot
            # remove it; report and treat as non-empty so ancestors stay.
            result.protected.append(current)
            return False
