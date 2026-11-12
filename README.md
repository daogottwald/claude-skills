# Claude Code skills for Firefox development

[Claude Code](https://claude.com/claude-code) skills for working on Firefox in mozilla-central: running tests, pushing to try, and getting patches through Phabricator review. Each skill targets a step where an agent's default approach gives a plausible but wrong result, such as a green test run that tested nothing or a re-push that pings reviewers about an unchanged diff.

## Skills

Testing:

- `run-mochitest`: run a mochitest locally and get a pass/fail you can trust, past stale objdirs, a display that leaks onto your desktop, port collisions with another run and harness flags that void a run.
- `test-triage`: find the tests that cover a change, run them, and return a compact verdict with the likely fix location.
- `try-push`: push to try with the right task selection, and read the result truthfully, retried failures included.

Review:

- `submit-single`: submit or update one patch with moz-phab, telling a real review request from a WIP first.
- `submit-stack`: push a rewritten multi-commit stack without un-WIPping commits or spamming reviewers, then repair the dependency edges moz-phab leaves stale.
- `stack-plan-series`: decide how to carve work into a reviewable series in which every prefix leaves the tree valid.
- `stack-assign-reviewers`: spread a stack's review requests across named reviewers by what each has reviewed before.
- `phab-comments`: read review comments on a revision or a stack, with "Suggest Edit" replacements shown as diffs, and post inline comments.
- `bugzilla-edit-comment`: edit an existing Bugzilla comment, comment 0 included, instead of posting a correction.

Code changes:

- `convert-subscript-to-module`: turn a chrome-window subscript into a `.mjs` or `.sys.mjs` module in commits that keep the file's history.
- `stylelint-add-rule`: add a custom rule to `stylelint-plugin-mozilla`, with its tests, docs and wiring.

Housekeeping:

- `prune-landed-branches`: delete local branches whose patches already landed, verified by content and screened for backouts.

`run-mochitest` relies on mozilla-central's in-tree `desktop-theme-probing` skill for probing a running Firefox, and `convert-subscript-to-module` mentions it too.

## Installing

The skills refer to their own scripts as `~/.claude/skills/<skill>/scripts/...`, so each one has to sit at that path. Clone the repo anywhere and symlink the skills you want:

```
git clone https://github.com/daogottwald/claude-skills.git ~/src/claude-skills
ln -s ~/src/claude-skills/run-mochitest ~/.claude/skills/run-mochitest
```

## Requirements

- A mozilla-central checkout with `./mach`.
- [moz-phab](https://github.com/mozilla-conduit/review) for `submit-single`, `submit-stack` and `stack-assign-reviewers`.
- A Phabricator API token in `~/.arcrc` (`moz-phab install-certificate` writes it) or in `PHABRICATOR_API_TOKEN`, for the scripts that call Conduit.
- A Bugzilla API key in `~/.config/python-bugzilla/bugzillarc` for `bugzilla-edit-comment`.
- [lrv](https://github.com/padenot/lrv/) for the local-review path in `submit-single` and `submit-stack`.
- On Linux, `xvfb-run` for an isolated display, and mutter for the compositing rung in `run-mochitest`.

## License

[MPL-2.0](LICENSE).
