#!/bin/bash
# Count what a single stylelint-plugin-mozilla rule flags across a mozilla-central
# checkout, without enabling it in .stylelintrc.js and without the rollout list.
#
# Usage: measure-rule.sh <rule-name> [path-or-glob ...]
#   measure-rule.sh no-has-selector
#   measure-rule.sh my-new-rule 'browser/**/*.css' 'toolkit/**/*.css'
#
# Writes the full report to artifacts/stylelint-measure-<rule>.log and prints a
# per-file count summary.

set -eu

RULE=${1:?usage: measure-rule.sh <rule-name> [path-or-glob ...]}
shift || true

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

if [ -x "$HOME/.mozbuild/node/bin/node" ]; then
  NODE="$HOME/.mozbuild/node/bin/node"
else
  NODE=$(command -v node)
fi

if [ $# -eq 0 ]; then
  set -- '**/*.{css,scss}'
fi

mkdir -p artifacts
CONFIG="artifacts/stylelint-measure-$RULE.config.js"
LOG="artifacts/stylelint-measure-$RULE.log"

cat > "$CONFIG" <<EOF
"use strict";
const fs = require("fs");
const path = require("path");
const ROOT = "$ROOT";
// ThirdPartyPaths/Generated entries are plain paths, mostly directories, while
// stylelint wants globs, so each entry is expanded to both forms.
const readList = p =>
  fs
    .readFileSync(path.join(ROOT, p), "utf-8")
    .split("\n")
    .filter(l => l && !l.startsWith("#"))
    .flatMap(l => [path.join(ROOT, l), path.join(ROOT, l, "**")]);
module.exports = {
  plugins: [
    path.join(ROOT, "tools/lint/stylelint/stylelint-plugin-mozilla/index.mjs"),
  ],
  ignoreFiles: [
    ...readList("tools/rewriting/ThirdPartyPaths.txt"),
    ...readList("tools/rewriting/Generated.txt"),
    // Local scratch dirs that .stylelintignore does not cover.
    path.join(ROOT, "artifacts/**"),
  ],
  rules: { "stylelint-plugin-mozilla/$RULE": true },
  overrides: [{ files: "**/*.scss", customSyntax: "postcss-scss" }],
};
EOF

"$NODE" node_modules/stylelint/bin/stylelint.mjs \
  --config "$CONFIG" \
  --formatter unix \
  --allow-empty-input \
  "$@" > "$LOG" 2>&1 || true

syntax=$(grep -c "CssSyntaxError" "$LOG" || true)
echo "Report: $LOG"
echo "Syntax errors (not your rule; a missing customSyntax or an invalid file): $syntax"
echo "Files with problems, most first:"
grep -E "\[(error|warning)\]" "$LOG" | grep -v CssSyntaxError | cut -d: -f1 |
  sed "s|^$ROOT/||" | sort | uniq -c | sort -rn | head -25
echo
tail -2 "$LOG"
