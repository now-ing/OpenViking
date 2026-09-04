// Collect stable entry ids for user messages from the host SessionManager.
//
// Hosts differ in how they expose the persisted context: pi exposes
// buildContextEntries(), while OMP (18.1.5+) exposes the equivalent entries
// through getBranch(). When neither API exists, return an empty list so the
// context hook skips stable-id mapping instead of throwing (#4652).
export function collectUserEntryIds(sessionManager) {
  const sm = sessionManager;
  let entries;
  if (sm && typeof sm.buildContextEntries === "function") {
    entries = sm.buildContextEntries();
  } else if (sm && typeof sm.getBranch === "function") {
    entries = sm.getBranch();
  } else {
    entries = [];
  }
  if (!Array.isArray(entries)) return [];
  // Positional placeholders (undefined) keep user-message index alignment
  // intact when an entry carries no usable id.
  return entries
    .filter((entry) => entry?.type === "message" && entry.message?.role === "user")
    .map((entry) => (typeof entry?.id === "string" ? entry.id : undefined));
}
