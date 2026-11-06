// docs/SAFETY.md as the site reads it: app/data/safety.json is this function's answer for the document, and
// app/tests/safety.test.mjs holds the file to it, so the page shows what the document says and nothing else.
// Pure: no window, no fetch.

const REPO = "https://github.com/namixai/colosseum-pools/blob/main/";

/** Where a source points, as a link: a line of the repository on GitHub, or Hyperliquid's documentation as it
 *  is. An address has none: the site's check-it-yourself page reads pools and challenges, not a factory or a
 *  registry, so the row shows the address and the call, which anyone can make (`cast call`). */
export function sourceHref(where) {
  const line = where.match(/^([\w./-]+):(\d+)$/);
  if (line) return `${REPO}${line[1]}#L${line[2]}`;
  if (/^0x[0-9a-fA-F]{40}$/.test(where)) return "";
  return where;
}

export function sourceKind(where) {
  if (/^[\w./-]+:\d+$/.test(where)) return "line";
  if (/^0x[0-9a-fA-F]{40}$/.test(where)) return "address";
  return "page";
}

// "- **Lead.** rest" with its continuation lines, as one item.
function items(block) {
  const out = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("- ")) out.push(line.slice(2));
    else if (line.startsWith("  ") && out.length) out[out.length - 1] += ` ${line.trim()}`;
  }
  return out.map((raw) => {
    const lead = raw.match(/^\*\*(.+?)\*\*\s*/);
    const text = lead ? raw.slice(lead[0].length) : raw;
    return { lead: lead ? lead[1] : "", text, sources: [...new Set(raw.match(/\bS\d+\b/g) || [])] };
  });
}

export function parseSafety(markdown) {
  const md = markdown.replace(/\r\n/g, "\n");
  const [head, ...parts] = md.split("\n## ");
  const lines = head.split("\n");
  const title = lines[0].replace(/^# /, "");
  const intro = lines.slice(1).join(" ").replace(/\s+/g, " ").trim();
  const sections = [];
  let sources = [];
  let sourcesNote = "";
  for (const part of parts) {
    const [heading, ...rest] = part.split("\n");
    const body = rest.join("\n").trim();
    if (heading === "Sources") {
      sourcesNote = body.split("\n\n")[0].replace(/\s+/g, " ").trim();
      for (const line of body.split("\n")) {
        const m = line.match(/^\| (S\d+) \| (.+?) \| `(.+)` \|$/);
        if (!m) continue;
        const where = m[2].replace(/^`|`$/g, "");
        sources.push({ id: m[1], where, kind: sourceKind(where), text: m[3].replace(/\\\|/g, "|"), href: sourceHref(where) });
      }
      continue;
    }
    const lead = body.split("\n- ")[0];
    sections.push({
      heading: heading.replace(/^\d+\.\s*/, ""),
      note: lead.startsWith("- ") ? "" : lead.replace(/\s+/g, " ").trim(),
      items: items(lead.startsWith("- ") ? body : body.slice(lead.length + 1)),
    });
  }
  return { title, intro, sections, sourcesNote, sources };
}
