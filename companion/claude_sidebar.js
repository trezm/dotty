// Used inside cua_repl after binding the Claude app. This reads only the
// accessible sidebar and emits booleans, never chat titles or message text.
function dottyClaudeSidebarStatus(ax) {
  const lines = ax.split("\n");
  const start = lines.findIndex(line => /\bcontainer Sidebar$/.test(line));
  if (start < 0) return {available: false, unread: false};
  const depth = line => (line.match(/^\t*/) || [""])[0].length;
  const base = depth(lines[start]);
  const sidebar = [];
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i].trim() && depth(lines[i]) <= base) break;
    sidebar.push(lines[i]);
  }
  const available = sidebar.some(line => /\bbutton Home(?:, unread activity)?$/.test(line)) &&
                    sidebar.some(line => /\bbutton Code(?:, unread activity)?$/.test(line));
  let unread = sidebar.some(line => /\bbutton (?:Home|Code), unread activity$/.test(line) ||
                                    /\bbutton Mark as read$/.test(line));
  const buttons = [];
  const clean = text => text.replace(/\s+/g, " ").trim();
  for (const line of sidebar) {
    const d = depth(line);
    while (buttons.length && buttons[buttons.length - 1].depth >= d) buttons.pop();
    const button = line.match(/^\s*\d+ button (.+)$/);
    if (button) buttons.push({depth: d, label: clean(button[1])});
    const text = line.match(/^\s*\d+ text (.+)$/);
    if (text && buttons.length) {
      const label = clean(text[1]), parent = buttons[buttons.length - 1].label;
      // Native Code rows append this hidden label. Comparing parent and child
      // avoids treating a chat merely named "Unread response" as a status.
      if (label !== parent && label.includes("Unread response") &&
          clean(label.replace("Unread response", "")) === parent) unread = true;
    }
  }
  return {available, unread: available && unread};
}

if (typeof module !== "undefined") module.exports = {dottyClaudeSidebarStatus};
