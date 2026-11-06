# Fork changes and upstream synchronization

This fork follows [xpengtoolkit/xpengtoolkit](https://github.com/xpengtoolkit/xpengtoolkit).
The current baseline is upstream commit `e0977b5`, release **0.6.42**.
The 6 November 2026 synchronization includes AC charge-current controls,
scheduled-charge state recovery, and invalid battery/temperature/tyre reading
guards from upstream releases 0.6.39 through 0.6.42.

Our contributions have been accepted upstream: command/control work and the card
in [PR #5](https://github.com/xpengtoolkit/xpengtoolkit/pull/5), Android command
corrections in [PR #7](https://github.com/xpengtoolkit/xpengtoolkit/pull/7), card
formatting in [PR #15](https://github.com/xpengtoolkit/xpengtoolkit/pull/15), and
startup I/O/watchdog diagnostics in
[PR #16](https://github.com/xpengtoolkit/xpengtoolkit/pull/16).
They now come directly from upstream's own commits.

Release **0.6.43** corrects the AC current-limit conversion on top of upstream
0.6.42. Commands subtract five from public amperes; the number, sensor and
TeslaMate export add five to the reported wire index. This is confirmed by the
official app's slider code and owner measurements on a P7+. See
[encoding evidence and upgrade notes](CHARGE_CURRENT_ENCODING.md). Drop this
behavior patch once upstream incorporates the correction.

Main is current upstream plus one consolidated fork patch. The
integration differs by the current-limit fix and its release version; the
bundled card, renewal server, second renewal-app slot and vendored sources match
upstream. The manifest also retains the fork issue-tracker URL.
Fork configuration includes the DrazorV publishing instructions, README banner,
development ignores, this provenance note, and a GHCR workflow that lowercases
the repository owner, with its regression test and documentation. The renewal
app is versioned independently. Original MIT notices, credentials compatibility
and Home Assistant entity IDs are preserved.

At the owner's request, main was aligned on 31 October 2026. Its previous tip,
`92a928b`, and ancestry are recoverable under
`archive/main-before-upstream-sync-20261031`. Earlier archives dated 24 and 29
October also remain. Existing release tags are never moved; release 0.6.35
continues to identify its original tested commit.

On 6 November 2026, the fork configuration was reapplied to upstream 0.6.42.
The previous main tip, `ae9d6fc`, remains recoverable under
`archive/main-before-upstream-sync-20261106`. Published tags and upstream PR
branches were preserved.

The 0.6.43 current-limit fix was consolidated into the fork patch on 6 November.
The previous tip `22cc533` is preserved by release tag `0.6.42` and
`archive/main-before-charge-current-fix-20261106`.

For future synchronization, fetch upstream and inspect both ancestry and actual
file differences. Keep a single consolidated fork patch on current upstream;
drop fixes from that patch once upstream accepts them. Do not accumulate merge
commits for changes upstream has squash-merged under other commit IDs. Before
any requested history realignment, preserve the old tip remotely, verify the
resulting tree and use an explicit expected old SHA when updating main. Preserve
open upstream PR branches and published release tags.

Install requirements-test.txt, then run `python3 tools/run_tests.py`,
`python3 -m unittest discover -s tests` and the reusable vehicle-card tests before
publishing integration changes.
