---
name: stylelint-add-rule
description: Add a custom CSS lint rule to mozilla-central's stylelint-plugin-mozilla — the rule module, its tests, its docs page, the .stylelintrc.js wiring, and what to do about existing failures. Use when asked to write, add, or enable a stylelint rule, to forbid or require a CSS pattern tree-wide, when deciding how strict it should be or whether it should autofix, or when a rule you added misbehaves — an autofix that never applies, a stylelint-disable comment that does not take, or a rule that throws during `mach lint`.
---

# Adding a stylelint rule to stylelint-plugin-mozilla

Custom rules live in `tools/lint/stylelint/stylelint-plugin-mozilla/`. Reach for one only when the check needs Gecko knowledge — `chrome://` URL layering, design tokens, `-moz-pref()` in media queries, a selector we consider too expensive. If upstream stylelint or an already-installed plugin has the rule, configuring it in `.stylelintrc.js` is the whole job and the rest of this skill does not apply.

Process, from `docs/code-quality/lint/linters/stylelint/enabling-rules.md`: file the bug under Developer Infrastructure :: Lint and Formatting, and get agreement from the Desktop Theme module owner and peers before landing. A new rule needs a plan for the existing failures, not just the rule.

## What to touch

| File | Why |
| --- | --- |
| `rules/<rule-name>.mjs` | the rule |
| `rules/index.mjs` | import plus a map entry, both alphabetical |
| `tests/<rule-name>.tests.mjs` | the tests; the name must match the rule |
| `docs/code-quality/lint/linters/stylelint-plugin-mozilla/rules/<rule-name>.md` | the page `meta.url` points at |
| `.stylelintrc.js` | enable it, alphabetical among the `stylelint-plugin-mozilla/*` entries |
| `stylelint-rollouts.config.js` | only when staging existing failures |
| the CSS itself | fixes, or `stylelint-disable-next-line` comments |

Nothing else needs registering. The plugin's `package.json` test script globs `tests/**`, the docs page lands in a `:glob:` toctree, and both CI tasks already watch the plugin directory: `stylelint` in `taskcluster/kinds/source-test/mozlint.yml` (runs `mach lint` over the tree) and `stylelint-plugin-mozilla` in `source-test/node.yml` (runs the plugin's own tests).

## The rule module

```js
/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import stylelint from "stylelint";
import { namespace } from "../helpers.mjs";

const {
  utils: { report, ruleMessages, validateOptions },
} = stylelint;

const ruleName = namespace("my-rule");

const messages = ruleMessages(ruleName, {
  rejected: thing => `${thing} is not allowed because ...`,
});

const meta = {
  url: "https://firefox-source-docs.mozilla.org/code-quality/lint/linters/stylelint-plugin-mozilla/rules/my-rule.html",
  fixable: false,
};

const ruleFunction = primaryOption => {
  return (root, result) => {
    const validOptions = validateOptions(result, ruleName, {
      actual: primaryOption,
      possible: [true],
    });

    if (!validOptions) {
      return;
    }

    root.walkDecls(decl => {
      report({ message: messages.rejected(decl.value), node: decl, result, ruleName });
    });
  };
};

ruleFunction.ruleName = ruleName;
ruleFunction.messages = messages;
ruleFunction.meta = meta;

export default ruleFunction;
```

`namespace()` from `helpers.mjs` prefixes the rule with `stylelint-plugin-mozilla/`; never hand-write that prefix. `helpers.mjs` also holds `SYSTEM_COLORS`, `isSystemColor`, `getLocalCustomProperties` (all `--foo: bar` declarations in the file, for resolving a local variable to its value), and the `isWord` / `isFunction` / `isUrlFunction` predicates for `postcss-value-parser` nodes.

Walking: `root.walkDecls`, `root.walkRules`, `root.walkAtRules("import", cb)`, `root.walkComments`. Parse a declaration value with `valueParser(decl.value)` rather than a regex when the value has functions or a shorthand list. `no-browser-refs-in-toolkit.mjs` is the model for URL checks, `use-design-tokens.mjs` for value checks.

Read `rule.raws.selector?.raw ?? rule.selector` when the source text matters — postcss normalizes `selector`, so casing and inline comments only survive in `raws`.

Where you report decides where a disable comment can go. A comment in the middle of a multi-line selector list is not a standalone comment node and disables nothing, so `no-has-selector` reports once on the rule node instead of once per `:has()`: one `stylelint-disable-next-line` before the rule then covers it. Make the same call deliberately for any rule that can match several times in one selector or value.

Pass `severity: "warning"` to `report()` for a finding that should not block landing. It is per-report, so one rule can error on the cases it is sure about and warn on the rest.

For an autofix, set `meta.fixable = true` and pass a `fix` callback that mutates the node:

```js
const fix = fixedValue !== null ? () => (decl.value = fixedValue) : undefined;
report({ message, node: decl, result, ruleName, fix });
```

Omit `fix` when you cannot produce a replacement that is itself valid under the rule; put the candidate in the message instead, the way `use-design-tokens` does with its suggested values. That snippet rewrites a value; a fix that *adds* a declaration is a different job, below.

## A rule's imports resolve from the root node_modules

At lint time the rule runs out of the checkout's top-level `node_modules`: the CI `stylelint` task only fetches the `node-modules` toolchain, and never runs `npm ci` in the plugin directory. The plugin's own `package.json` is installed only for its test task — and locally, where it then shadows the root copy.

Two consequences. Anything a rule imports has to be resolvable from the root `node_modules`, which in practice means a transitive dependency of stylelint (`postcss`, `postcss-value-parser`) or a new entry in the root `package.json`; declaring it only in the plugin's `package.json` gives you a rule that passes its tests and throws in CI. And a version pinned in the plugin's `package.json` is the version the tests see, not the version the lint run sees: `@csstools/media-query-list-parser` is 4.0.2 in the plugin and 3.0.1 at the root today, and `media-query-no-invalid` works only because the exports it uses exist in both.

Check with `ls node_modules/<pkg>` at the repo root, and confirm the lint path itself by running `./mach lint -l stylelint` on a file that trips the rule.

The plugin's own JS is linted with browser globals in scope, so `name` as a variable or parameter fails `no-shadow` with `'name' is already a global variable` — a plain `for (let name of tokenNames)` in `helpers.mjs` is enough to trip it. Name it `tokenName`.

## Tests

`tests/<rule-name>.tests.mjs`, using `stylelint-test-rule-node` on top of `node --test`:

```js
// Bug 1948378: remove this exception when the eslint import plugin fully
// supports exports in package.json files
// eslint-disable-next-line import/no-unresolved
import { testRule } from "stylelint-test-rule-node";
import stylelint from "stylelint";
import myRule from "../rules/my-rule.mjs";

let plugin = stylelint.createPlugin(myRule.ruleName, myRule);
let {
  ruleName,
  rule: { messages },
} = plugin;

testRule({
  plugins: [plugin],
  ruleName,
  config: [true],
  fix: false,
  accept: [{ code: ".a { color: red; }", description: "..." }],
  reject: [
    {
      code: ".a { color: blue; }",
      message: messages.rejected("blue"),
      description: "...",
      line: 1,
      column: 6,
    },
  ],
});
```

The `eslint-disable-next-line import/no-unresolved` above the helper import is required; without it `mach lint -l eslint` fails on the test file.

Don't compute a reject case's `column` by hand — it is not where the property starts. Write the case without it, run the file once, and take the `actual:` value out of the failure.

Under `fix: true` every reject case must carry `fixed` (the expected output) or `unfixable: true`, so a fixable rule usually needs two `testRule` calls: one `fix: false` for the reporting cases, one `fix: true` for the fixes. `use-design-tokens.*.tests.mjs` splits per property as well, since one rule's test file would otherwise be thousands of lines.

Run one file while iterating, which is instant:

```
~/.mozbuild/node/bin/node --test tools/lint/stylelint/stylelint-plugin-mozilla/tests/my-rule.tests.mjs
```

Run the whole suite the way CI does (about 2s, ~720 cases):

```
./mach npm test --prefix tools/lint/stylelint/stylelint-plugin-mozilla
```

That path needs the plugin's dependencies installed once: `./mach npm ci --prefix tools/lint/stylelint/stylelint-plugin-mozilla`. Its treeherder-format reporter prints `TEST-PASS | undefined | <case>` for every case — the file-path column is broken for all rules, not a symptom of yours.

## Measure before choosing what the rule checks

```
~/.claude/skills/stylelint-add-rule/scripts/measure-rule.sh <rule-name> [glob ...]
```

The script writes a scratch config that loads the plugin with your rule as the only rule, honours `.stylelintignore` plus `ThirdPartyPaths.txt` and `Generated.txt`, applies `postcss-scss` to `.scss`, and prints the worst files first. Tree-wide it takes about 12s; the report stays in `artifacts/stylelint-measure-<rule>.log`. Pass `'**/*.css'` when the rule is not meant for SCSS — measuring one rule flat over the tree ignores the exemptions the real config would give it, which is why `media-query-no-invalid` measures 157 hits it never reports in practice (the real config nulls it for SCSS).

Without `postcss-scss`, every SCSS file reports `Unknown word // (CssSyntaxError)`; the script counts those separately for that reason.

**`.stylelintignore` excludes by extension, so the big SCSS directories are in scope.** It lists `browser/extensions/newtab/**/*.css` and the `aboutwelcome` / `asrouter` equivalents — the `.css` only. Every `.scss` file in those same directories is linted, and since that is where the SCSS lives, a rule aimed at the whole tree often finds most of its hits there: a color rule's sweep landed 50 declarations across 35 files, 23 of them in newtab and asrouter SCSS, against a hand estimate over `.css` alone that had predicted 24. So never size a rule from `.css`, and treat `measure-rule.sh`'s number as the count. Those directories also `extends: "stylelint-config-standard-scss"`, which brings formatting rules the rest of the tree does not have — see the insertion mechanics under Enabling.

**A sweep that edits `aboutwelcome` or `asrouter` SCSS must regenerate the checked-in CSS in the same commit.** Their compiled `.css` is tracked, and the asrouter node job's `bundles` check (`browser/components/asrouter/bin/try-runner.js`) rebuilds it and fails on any difference, which neither stylelint nor the plugin tests see; bug 2074413 was backed out for it (`asrouter/content-src/styles/_multistage.scss` compiles into both `aboutwelcome.css` and `asrouter-newtab-multistage.css`). Run `./mach npm ci` then `./mach npm run bundle:css --prefix=browser/components/asrouter` and `./mach npm run bundle:welcomeCss --prefix=browser/components/aboutwelcome`, and check with `node bin/try-runner.js` from `browser/components/asrouter` with `~/.mozbuild/node/bin` first on `PATH` (the karma and coverage steps need a Firefox binary and fail locally regardless). New Tab's bundle check compares only prerendered HTML, so its SCSS needs nothing.

Once the rule is wired into `.stylelintrc.js`, `./mach lint -l stylelint .` covers the tree in about 35s, and `--skip-rollouts` shows what the rollout list is hiding. Take a baseline first: a local branch can leave hundreds of unrelated failures in the tree, so diff by rule name rather than trusting a total.

Measure the rule's *semantics*, not just its hit count. A check you can state in one English sentence usually has two or three defensible strictnesses, and which one is right is an empirical question about the tree rather than a matter of taste. So gate the wider checks behind a temporary `process.env` flag, measure each, and delete the gates before landing — one pass per variant is seconds, and the counts differ by orders of magnitude (a pairing rule measured 1, 9 and 155 depending on how much it demanded).

Counts alone still don't decide it, because a broad check's findings are not interchangeable: some are fixable, some are already satisfied via the cascade, and some are impossible to satisfy where the rule reports. **Classify a sample of 30 before committing to a check** — a subagent reading the surrounding CSS returns the ratio without the file dumps reaching you. Two thirds impossible or wrong is a rule to narrow, not a rollout to stage. A ratio like that is also the argument the bug and the reviewer need for why the obvious stricter rule is not the one you wrote, so keep it.

**Resist a carve-out for `::part()`.** It looks like the clearest exemption there is — the part belongs to another component's shadow tree, so surely that component owns its text color and a rule reaching in to repaint the background can be excused. It is wrong: an outer-tree `::part()` rule **overrides** the inner tree's own declaration for that part, so the call site can simply declare both halves in the same block. `toolkit/content/widgets/moz-select/moz-select.css` does exactly that — its `panel-item[selected]::part(button)` sets `background-color` *and* `color` on the very element `panel-item.css` gives `background-color: transparent; color: inherit`. (Specificity is on its side there as well, but it does not need to be: for normal declarations the outer tree wins over the inner one regardless.) A `::part()` carve-out therefore hides findings the author could have fixed in one line, and every site it silences reads as convincingly exempt while you are looking at it.

**An at-rule nested in a rule styles the same element; a nested rule styles a different one.** A check that wants a declaration present on the element has to tell those apart, because the surrounding block sometimes already supplies it: an `@media` or `@supports` nested inside a rule takes that rule's `color`, so the block is satisfied and needs no exemption, while a nested rule matches a different element and stands on its own. An exemption keyed on the nesting ancestor without that distinction goes wrong in one direction or the other — it either excuses every nested rule or reports every conditional block.

**A rule keyed on a generated table inherits that table's blind spots, and a green tree is not a covered tree.** Two things worth measuring besides the hit count. *Coverage of the table*: of the 123 `background-color` design tokens only 69 have the text counterpart the pairing rules key on, and the helper's name regex misses five real pair shapes outright — a family in an entirely different naming scheme, a family filed under the wrong category, state-before-property word order, a missing hyphen, and a non-greedy split that sends `--input-text-background-color` hunting for `--input-text-text-color` while its real sibling parses into a different family. *Reach past the declaration*: a rule that inspects the declaration which **reads** a token is blind both to indirection (a local `--alias: var(--token)` — 25 declarations resolvable within one file, 73 tree-wide) and to redefinition (a block that **defines** a token-named custom property to remap a component's palette — 219 sites, against the 46 the rule itself reported). Say which of those the rule does not cover, in the docs page and in the bug, or the next reader takes the green run for a guarantee. The table also moves under you: `semantic-categories.mjs` is generated by `./mach buildtokens`, so a pull touching any `*.tokens.json` can change a hit count with no change to the rule or to the CSS — re-measure before quoting a number you took last week.

Scratch CSS you write to exercise the rule goes outside the repo (`/tmp`), not in `artifacts/`. The measure script ignores `artifacts/**` explicitly, but `.stylelintignore` does not, so a scratch file there is linted by the real `./mach lint -l stylelint .` run and inflates the tree-wide count with findings that look like real ones.

## An autofix that adds a declaration

Two questions, and the first one is empirical.

**Whether to fix at all.** "The right value cannot be inferred" is the standard reason to leave a rule unfixable, and it is often false: if the rule only fires when a deterministic replacement exists — which is also what lets the message name that replacement — then the fix is determined, and the real question is whether *applying* it is safe. That is answerable from the sweep the rule already needed. For every declaration the sweep added, check whether the fix would have produced it, and whether it would also have fired on the sites the sweep annotated instead; `git show <sweep-rev>^:<file>` gives the pre-patch file to test a candidate gate against. Report it as firings / correct / wrong, for the ungated fix and for each gate under consideration, and put that table in the bug.

**Distrust a locality gate until it is measured.** "Only fix where the counterpart already appears nearby" reads as obviously safer and can be anti-correlated with correctness. On one rule the single site the fix got wrong sat 5 lines from the nearest use of the very token it should not have inserted — nearer than 24 of the 28 sites it got right — because the rule immediately below it was the one handing the color to a child element. What makes a site an exception is often *in its neighborhood*, which is exactly what a proximity heuristic reads as positive evidence. That gate would have given up 16 correct fixes to avoid 1 wrong one, so the ungated fix is what shipped.

What carries the safety instead is already in stylelint. `isFixDisabled` consults the disabled ranges for fixes as well as for reports, so an existing `stylelint-disable-next-line` blocks the insertion and every site a sweep annotated stays protected from a later `--fix`. And `disableFix: true` as a rule option — `[true, { disableFix: true }]` in `.stylelintrc.js` — sits in stylelint's `IGNORED_OPTIONS`, so it passes a rule's own `validateOptions` untouched: a per-directory opt-out for a risky fix, with no rule-side code.

**How to insert one.**

```js
fix: background.next()?.type == "comment"
  ? undefined
  : () => background.cloneAfter({ prop: "color", value: `var(${counterpart})` }),
```

- **Clone, never construct.** `cloneAfter` carries `raws.before` and `raws.between` over, so the new line takes its sibling's own indentation and a single-line block stays on one line. A node without `raws.before` gets postcss's inference instead, and that is file-global: it takes the first declaration *anywhere* in the file, caches the result on `root.rawCache`, and falls back to a four-space indent. Core's `declaration-block-no-redundant-longhand-properties` clones for the same reason; no rule in the plugin inserts a declaration yet.
- **Insert after, never before.** `cloneBefore` can land the new declaration between a `stylelint-disable-next-line` and the line it annotates, silently retargeting it. Inserting after cannot.
- **Bail when the next sibling is a comment.** postcss models a trailing comment as the following sibling, so an insertion reorders it onto the new line.
- `report()` throws outright if `fix` is passed while `meta.fixable` is falsy, so flipping the metadata is mandatory rather than cosmetic. Pass a bare callback, not the deprecated `context.fix`, which warns as soon as `fixable` is true.
- Secondary formatting largely settles itself. Every plugin rule sorts ahead of every core rule (plugin names are absent from stylelint's `rulesOrder`, so `indexOf` returns -1), so a core fixer runs after your insertion within the same pass; `declaration-empty-line-before`'s `except: ['after-declaration']` means a cloned single-newline `before` already satisfies it; and mozlint runs prettier immediately after stylelint `--fix`. That last safety net is `.css` only — `.prettierignore` never re-includes `.scss` — so SCSS is where the raws have to be right on their own.

## A second check on the same subject: its own rule, or a secondary option?

When the new check is adjacent to a rule that already exists — same tokens, same declarations, often the same block scan — extending that rule with a secondary option looks like the economical move. Decide it on **suppression granularity**, not on code reuse, because every off switch stylelint offers is keyed by rule name:

- A `stylelint-disable-next-line stylelint-plugin-mozilla/<rule>` comment names the rule, not the message, so it silences *every* check that rule performs on that line — including ones that start applying to the block later, after someone adds a declaration.
- An `overrides` entry in `.stylelintrc.js` and a `stylelint-rollouts.config.js` entry both key on the rule name too, so per-directory staging is per-rule.
- `meta.fixable` is per-rule. Folding an unfixable check into a `fixable: true` rule makes the metadata claim something untrue of part of what it reports.

So the question is whether the two checks get suppressed at the same sites. If a site that legitimately silences one still wants the other, they are two rules.

**Ask first whether the two can fire on the same site at all.** If they can't, no disable comment could ever cover both, granularity says nothing about them, and the argument for splitting is gone. The worked case ran both ways. `use-paired-color-tokens` reports a background and text token that are not counterparts, and the companion check — this block paints a surface and declares no text color at all — was split into its own rule because that check's exceptions are surfaces inheriting their text color from an ancestor, and annotating one of those must not switch off counterpart checking on the block forever. But the pairing check returns early unless the block declares both halves, and the companion only fires when the text half is absent, so no block can report both; they also report on different declarations. Every disable comment in the tree named one of the two. The `meta.fixable` half of the argument expired as well once the companion check gained an autofix, and bug 2069552 folded them back into one rule under the pairing name. So a split needs the two checks competing for one disable comment; their exceptions differing is not by itself that.

**Merging two rules back into one:** reconcile the test suites rather than concatenating them, because each suite's accept cases include ones asserting that the other rule's violation is not this rule's business, and those flip to rejects. And sweep the whole tree for the retired name's disable comments, not just the directories the rule was measured over — one of five lived in `mobile/`, outside every path the original sweep had touched. Re-sweep on every rebase as well: new CSS keeps landing with the retired name, so a merge commit that was green when written picks up a fresh error each time the base moves, and the tree-wide run is the only thing that finds it.

A secondary option is right when it *tunes the same check* rather than adding a second one — `media-query-no-invalid`'s `ignoreFunctions` is the in-tree precedent, and its shape (`validateOptions` taking a second schema object with `optional: true`, the rule function taking `(primary, secondaryOptions)`) is what to copy when that is the case.

The cost of splitting is the scan both rules need. Lift it into `helpers.mjs` as its own commit before the new rule, so the new rule's diff is only the new rule.

## Enabling, and staging the failures

Add the rule to the top-level `rules` block in `.stylelintrc.js` alphabetically. A directory-scoped exemption goes in `overrides` with a `name:` describing it, next to the existing `design-token-rules-off` / `has-selector-rule-off` blocks. Rollout entries must stay last in `overrides`, which is what `...rollouts` at the end is for.

Pick a shape from `enabling-rules.md` — fix everything up front, enable everywhere with inline disables, enable as a warning, or error on new code and warn on existing. The two in-tree precedents: `no-has-selector` landed with a `stylelint-disable-next-line` comment in each of ~50 CSS files, which suits a count small enough for each site to deserve a human decision; `use-design-tokens` uses a `stylelint-rollouts.config.js` entry per area because the count runs into the thousands. A rollout entry is `{ name, rules, files }` and needs follow-up bugs filed before landing, blocking bug 1762027.

Every inline disable carries a comment above it saying which of the two it is, because the two have opposite futures and nothing else in the tree distinguishes them:

```css
/* TODO(bug NNNNNN): the hover text color belongs on this block too. */
/* stylelint-disable-next-line stylelint-plugin-mozilla/use-paired-color-tokens */
color: var(--panel-text-color);

/* The row background lives on ::-moz-tree-row, so this block cannot carry it. */
/* stylelint-disable-next-line stylelint-plugin-mozilla/use-paired-color-tokens */
color: var(--text-color-list-item-hover);
```

**Prefer the one-line fix to the disable, and distrust a justification that reasons about the block's contents.** "This element renders no text", "its icon takes the color from the `fill` below", "a child colors itself" all sound conclusive and are all easy to falsify: the markup changes, or `forced-colors` turns a translucent wash into an opaque `ButtonFace` and the inherited value stops being safe. So does a justification that only holds in the default theme. Writing the declaration instead costs one line, is usually value-neutral where the token is a `currentColor` wash, and stays true — on one rule 49 of 54 sites took the declaration and only 5 kept a disable: the blocks that declare the color on a different element (a tab's background and its content are siblings), that paint no text at all, where one would do damage (it would recolor an icon beside the text), or where it would not resolve (the page defines the token name locally and loads no token sheet). Reserve the disable for those.

A real violation the patch defers gets a `TODO(bug NNNNNN)` naming a filed follow-up bug; a site the rule is simply wrong about gets a one-line justification. Earlier rollouts (`no-has-selector`) landed bare disable comments, which is why nobody can now tell a deferred fix from a permanent exception without re-deriving the judgement — so annotate even when it doubles the diff. A rule whose exceptions outnumber its deferred violations is telling you the check is too broad: narrow it and re-measure rather than writing the justifications.

Two mechanics of inserting the comment pair, both silent until the tree-wide run:

- **Never land the pair between an existing `stylelint-disable-next-line` and its declaration** — that orphans the existing disable, and the rule it was suppressing starts firing somewhere you were not looking. Merge the names into one comment instead: `/* stylelint-disable-next-line plugin/rule-a, plugin/rule-b */`, with the justification above it.
- **Two rules in the `stylelint-config-standard-scss` directories police the blank lines around your comment.** `comment-empty-line-before` wants one above the comment and `declaration-empty-line-before` wants none above the declaration, so *adding* a pair needs a blank line inserted and *removing* one leaves a stray blank line behind. `./mach lint -l stylelint --fix <dirs>` settles both; script the insertion and let `--fix` clean up rather than getting the whitespace right by hand.

**Reviewbot lints each commit without its stack parent, so distrust its count on a rule-plus-sweep stack.** The enabling commit sits on top of the sweep, and the bot evaluates it against a tree the sweep has not been applied to, reporting every site the sweep already fixed. Nothing in its output says the base is wrong. Check locally over the range instead, and expect to explain the bot's number in the review.

## The docs page

`docs/code-quality/lint/linters/stylelint-plugin-mozilla/rules/<rule-name>.md`, with the filename matching `meta.url` — the error message links straight there, so a mismatch ships a 404 to everyone who hits the rule. The parent page's toctree is a glob, so no index edit. Follow the existing pages: what the rule disallows and why, its scope and exemptions, incorrect and correct examples, and when disabling it is acceptable, with the exact disable comment.

## Before submitting

- `./mach lint -l stylelint .` over the whole tree. This is what the tier-1 task runs, and it is 35s. The first landing of `no-has-selector` was backed out for one missed failure in `toolkit/themes/shared/aboutNetError.css`.
- `./mach npm test --prefix tools/lint/stylelint/stylelint-plugin-mozilla`. The `media-query-no-invalid` fork was backed out for a failure in this task alone.
- `./mach lint -l eslint tools/lint/stylelint/stylelint-plugin-mozilla` — the rule and its tests are linted like any other JS, and `--fix` also formats them.
- For a fixable rule, run `./mach lint -l stylelint --fix` on a scratch copy of a failing file and re-lint the result: a fix that produces output the rule still rejects is worse than no fix.
- Reviewers: the Desktop Theme group for the rule and the config, plus the owners of every CSS file you touched — the `no-has-selector` patch carried a dozen reviewer groups for that reason. mozilla-central's in-tree `.claude/skills/find-reviewer` covers the per-file part.
