'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function canonicalPath(value) {
  const absolute = path.resolve(value);
  let existing = absolute;
  const suffix = [];
  while (!fs.existsSync(existing)) {
    const parent = path.dirname(existing);
    if (parent === existing) return absolute;
    suffix.unshift(path.basename(existing));
    existing = parent;
  }
  return path.resolve(fs.realpathSync(existing), ...suffix);
}

function insideOrSame(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

function expandHome(value, home) {
  if (value === '~') return home;
  if (value.startsWith(`~${path.sep}`)) return path.join(home, value.slice(2));
  return value;
}

function isolatedStateError(env = process.env) {
  const home = os.userInfo().homedir;
  const live = canonicalPath(path.join(home, '.local', 'state', 'briefing'));
  if (env.BRIEFING_TESTING !== '1') return 'set BRIEFING_TESTING=1';
  if (typeof env.BRIEFING_STATE_DIR !== 'string' || !env.BRIEFING_STATE_DIR.trim()) {
    return 'set BRIEFING_STATE_DIR to an isolated directory';
  }
  let candidate;
  try {
    candidate = canonicalPath(expandHome(env.BRIEFING_STATE_DIR, home));
  } catch {
    return 'set BRIEFING_STATE_DIR to a valid isolated directory';
  }
  if (insideOrSame(candidate, live) || insideOrSame(live, candidate)) {
    return 'BRIEFING_STATE_DIR must not overlap ~/.local/state/briefing';
  }
  return null;
}

function enforceTestEnvironment() {
  const error = isolatedStateError();
  if (!error) return;
  process.stderr.write(`Refusing to run briefing tests: ${error}.\n`);
  process.exit(2);
}

module.exports = { enforceTestEnvironment, isolatedStateError };
