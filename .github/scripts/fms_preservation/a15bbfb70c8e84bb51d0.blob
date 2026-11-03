# Remote branch picker

Change Branch lists the currently checked-out local branch and branch names from
`ajouatom/openpilot` and `fullmetalsonic/openpilot`. Listing uses `git ls-remote
--heads`: it does not fetch objects, write remote configuration, prune refs, or
take the repository mutation lock. A failed remote is shown as a warning while
the current branch and successful remote remain available.

The upstream is labelled `origin` when that remote already points to ajouatom;
otherwise it is a virtual `ajouatom` source. The fork source is `fullmetalsonic`.
An origin pointing to the fork is preserved, and duplicate repository groups are
not added. Remote configuration is prepared only when selecting a remote branch.

Remote selection fetches that branch with an explicit refspec, no tags and no
submodule recursion, then switches to its local branch. Existing local branches
must track the same repository and branch; they are not reset or merged by this
operation. Use the existing pull action to update an existing local checkout.
The selected branch still needs its required Git history and files. Local
selection does not fetch. Mutation actions retain the shared repository lock.

Git sync, after its confirmation, deletes other local branches and prunes stale
remote-tracking entries. It does not download branch contents or delete working
files, runtime files, or Params. The underlying Git command refuses to delete a
branch used by another worktree.

Verification: 17 focused Python cases, including real temporary Git repositories,
verify unchanged config/refs/objects during listing, selective fetch with a
single-branch refspec, same-repository aliases, malformed selection rejection,
partial failures and cleanup. A Node test verifies the partial-failure display.
These host tests do not establish installation or vehicle operation on a device.
