# recursive-directory-pruner

Removes empty directories from a tree, walking bottom-up so that a chain of
nested empty folders collapses in a single pass. Optionally bounds the walk
by depth and by a path prefix.

## Usage

```python
from recursive_directory_pruner import Pruner, PruneResult

pruner = Pruner(max_depth=10, stop_at="/srv/protected", remove_root=False)
result: PruneResult = pruner.prune("/var/cache/builds")

print(result.removed)     # directories successfully rmdir'd
print(result.kept)        # directories left standing (had content)
print(result.protected)   # directories we could not remove
print(result.root_kept)   # bool: does the root still exist?
```

`Pruner` accepts `max_depth` (int or None), `stop_at` (absolute path prefix;
directories at or beneath it are never removed), and `remove_root` (bool,
default False). `prune(path)` returns a `PruneResult` with `removed`,
`kept`, `protected`, and `root_kept` fields.

## Why this exists

Build systems and download caches accumulate empty directories after their
contents are deleted. A single sweep that removes the husks without
touching directories that still hold files is the whole point. The trade-off
here is simplicity over cleverness: there is no stat-caching, no inode
pooling, and no watcher. You call `prune()` when you want a sweep; the
walk is O(tree size) each time. For a CI cache of a few hundred thousand
nodes that is fine; for a filesystem-scale crawl you want something else.

## Edge cases worth knowing

- A symlink to a directory is treated as a **leaf**, not traversed. Its
  presence keeps its parent directory alive. This avoids loops on cyclic
  trees and behaves sanely on Windows where symlinks are privileged.
- `max_depth=0` inspects only the root's immediate children and removes
  nothing deeper. It also never removes the root. Use it as a "just the
  top layer" sweep.
- The root is never removed unless you pass `remove_root=True`. With the
  default `False`, running `prune()` against an already-empty root is
  idempotent and leaves the root standing.
- Directories that cannot be removed (permission denied, content appeared
  between the walk and the `rmdir`) are reported in `result.protected` and
  treated as non-empty so their ancestors are kept.

## Performance

The window keeps a bounded buffer, so `push` is constant time and memory does not
grow with the length of the stream. `peak` and `trough` are linear in the window
size, which is the trade that keeps `push` cheap.

