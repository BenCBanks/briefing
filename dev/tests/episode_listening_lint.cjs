require('../test_guard.cjs').enforceTestEnvironment();

// Keep the Python fixture tests inside the isolated browser harness environment.
const { spawnSync } = require('node:child_process');

const suites = new Map([
  ['episode_script_checks_test', 'dev.tests.episode_script_checks_test'],
  ['episode_generation_test', 'dev.tests.episode_generation_test'],
  ['episode_e2e_test', 'dev.tests.episode_e2e_test'],
]);
const selected = process.argv[2];
if (selected && !suites.has(selected)) {
  throw new Error(`Unknown focused suite: ${selected}`);
}
const modules = selected ? [suites.get(selected)] : [...suites.values()];
const result = spawnSync('python3', ['-m', 'unittest', '-q', ...modules], {
  cwd: process.cwd(), env: process.env, encoding: 'utf8',
});
if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
