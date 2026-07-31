// flowgate.default.0371 T0014 (NR0007 §5): "skip the permission confirmation" is not a
// stored setting — it is a flag inside the free-text CLI command, which is how it came to
// sit in the suggested command of every provider without anyone reading it as a security
// choice. The editor now renders it as its own control, and this module is the translation
// between that checkbox and the command string.
//
// The flags themselves are NOT duplicated here: they arrive with the settings catalog
// (`catalog.cli_permission_skip`, built by ai_settings_service), so a CLI's flag only ever
// changes in one place. What lives here is the same edit the server makes, so the box can
// be ticked and seen in the command box before anything is saved.

function escapeForRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Shell-word boundaries, not \b: `--yolo` must not match inside `--yolo-mode`, and
// `--ask-for-approval never` must not match `--ask-for-approval never-mind`. The leading
// boundary is a capture group rather than a lookbehind, so a removal takes the separating
// space with it and nothing depends on lookbehind support.
function tokenPattern(token, flags = '') {
  const body = token.trim().split(/\s+/).map(escapeForRegExp).join('\\s+');
  return new RegExp(`(^|\\s)(?:${body})(?=\\s|$)`, flags);
}

function insertAfterProgram(cmd, token) {
  // Both CLIs take these as global options that precede the subcommand (`codex ... exec`),
  // and a working command usually ends in a bare `-` meaning "prompt on stdin" — appending
  // there would hand the flag to the wrong parsing stage.
  const match = cmd.match(/^(\S+)\s+([\s\S]+)$/);
  if (!match) return `${cmd} ${token}`;
  return `${match[1]} ${token} ${match[2]}`;
}

/** The rule for this kind, or null when the CLI has no flag we know of (copilot, custom). */
export function permissionSkipRule(catalog, kind) {
  return (catalog && catalog.cli_permission_skip && catalog.cli_permission_skip.rules
    && catalog.cli_permission_skip.rules[kind]) || null;
}

/** True when the command tells this CLI not to ask before it reads, writes or runs. */
export function hasPermissionSkip(catalog, kind, command) {
  const rule = permissionSkipRule(catalog, kind);
  const cmd = (command || '').trim();
  if (!rule || !cmd) return false;
  return (rule.markers || []).some((marker) => tokenPattern(marker).test(cmd));
}

/**
 * The command with permission confirmation switched off (`enabled`) or back on.
 *
 * Rewrites in place where it can, so ticking the box twice gives the original string back.
 * A kind with no known flag, or an empty command, is returned untouched.
 */
export function setPermissionSkip(catalog, kind, command, enabled) {
  const rule = permissionSkipRule(catalog, kind);
  const cmd = (command || '').trim();
  if (!rule || !cmd) return cmd;

  if (enabled) {
    if (hasPermissionSkip(catalog, kind, cmd)) return cmd;
    if (rule.safe && tokenPattern(rule.safe).test(cmd)) {
      return cmd.replace(tokenPattern(rule.safe), (match, lead) => `${lead}${rule.skip}`);
    }
    return insertAfterProgram(cmd, rule.skip);
  }

  if (!hasPermissionSkip(catalog, kind, cmd)) return cmd;
  let next = cmd;
  for (const marker of rule.markers || []) {
    next = next.replace(tokenPattern(marker, 'g'), '').trim();
  }
  // Dropping codex's flag would leave the policy unnamed, which is not the same promise as
  // "it asks", so the safe policy is spelled out again — but only on a command that really
  // did carry a skip.
  if (rule.safe && !next.includes(rule.safe.split(/\s+/)[0])) {
    next = insertAfterProgram(next, rule.safe);
  }
  return next.trim();
}
