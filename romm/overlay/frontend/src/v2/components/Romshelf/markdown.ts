// Copy of RomStop's src/lib/markdown.ts (the site's Markdown parser). Keep the two in step.

const tagRx = (names: string) => new RegExp(`\\[(/?)(?:${names})(?:=[^\\]]*)?\\]`, "gi");

/** BBCode or forum HTML to Markdown. Text that's already Markdown passes through. */
export function bbcodeToMarkdown(input?: string | null): string {
  let s = (input ?? "").replace(/\r\n?/g, "\n");
  // Forum HTML: line breaks and paragraphs first, then the tags we can express.
  s = s
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/?p[^>]*>/gi, "\n\n")
    .replace(/<li[^>]*>/gi, "\n- ")
    .replace(/<\/?(ul|ol)[^>]*>/gi, "\n")
    .replace(/<h[1-6][^>]*>([\s\S]*?)<\/h[1-6]>/gi, "\n\n### $1\n\n")
    .replace(/<(b|strong)>([\s\S]*?)<\/\1>/gi, "**$2**")
    .replace(/<(i|em)>([\s\S]*?)<\/\1>/gi, "*$2*")
    .replace(/<a [^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi, "[$2]($1)")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;|&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");

  s = s
    // Links: [url=x]text[/url], [url]x[/url], [youtube]id[/youtube]
    .replace(/\[url=["']?([^\]"']+)["']?\]([\s\S]*?)\[\/url\]/gi, (_, href: string, text: string) => {
      const t = text.trim();
      return !t || t === href ? href : `[${t}](${href})`;
    })
    .replace(/\[url\]([\s\S]*?)\[\/url\]/gi, "$1")
    .replace(/\[youtube\]([\w-]+)\[\/youtube\]/gi, "https://youtu.be/$1")
    .replace(/\[img[^\]]*\][\s\S]*?\[\/img\]/gi, "")
    // Emphasis. Spaces inside the markers would stop Markdown from seeing them, so move them outside.
    .replace(/\[b\]([\s\S]*?)\[\/b\]/gi, (_, t: string) => wrap(t, "**"))
    .replace(/\[(?:i|em)\]([\s\S]*?)\[\/(?:i|em)\]/gi, (_, t: string) => wrap(t, "*"))
    .replace(/\[(?:s|strike)\]([\s\S]*?)\[\/(?:s|strike)\]/gi, (_, t: string) => wrap(t, "~~"))
    .replace(/\[code\]([\s\S]*?)\[\/code\]/gi, (_, t: string) => (t.includes("\n") ? `\n\n${t.trim().split("\n").map((l) => "    " + l).join("\n")}\n\n` : wrap(t, "`")))
    .replace(/\[h[1-6]\]([\s\S]*?)\[\/h[1-6]\]/gi, "\n\n### $1\n\n")
    .replace(/\[quote(?:=[^\]]*)?\]([\s\S]*?)\[\/quote\]/gi, (_, t: string) => `\n\n${t.trim().split("\n").map((l) => "> " + l).join("\n")}\n\n`)
    .replace(/\[hr\]/gi, "\n\n---\n\n")
    // Lists: every [*] starts an item; [list] just opens a block.
    .replace(/\[\/?list(?:=[^\]]*)?\]/gi, "\n")
    .replace(/\s*\[\*\]\s*/g, "\n- ")
    .replace(/\[\/\*\]/g, "")
    // Presentation-only tags carry no meaning in Markdown.
    .replace(tagRx("u|center|left|right|justify|size|color|colour|font|spoiler|sub|sup|li|ul|ol|indent|align|noparse|td|th"), "")
    .replace(tagRx("br|table|tr"), "\n")
    // "•" bullets from older imports.
    .replace(/^[ \t]*•[ \t]*/gm, "- ");

  return s
    .split("\n")
    .map((l) => l.replace(/[ \t ]+/g, " ").replace(/^ (?=\S)/, "").trimEnd())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function wrap(text: string, mark: string) {
  const m = text.match(/^(\s*)([\s\S]*?)(\s*)$/)!;
  return m[2] ? `${m[1]}${mark}${m[2]}${mark}${m[3]}` : text;
}

/**
 * Some sources (an early SMW Central scrape) lost every line break. Put back the structure the
 * markup still shows: bold "Heading:" lines start a paragraph, and " - " runs after one become a list.
 */
export function restoreStructure(md: string): string {
  if (md.includes("\n")) return md;
  return md
    .replace(/\s*(\*\*[^*\n]{1,60}:\*\*|\*\*[^*\n]{1,60}\*\*:)\s*/g, "\n\n$1\n")
    .split("\n\n")
    .map((block) => {
      const [head, ...rest] = block.split("\n");
      const body = rest.join("\n");
      if (/^\s*[-*•] /.test(body) && (body.match(/ [-•] /g)?.length ?? 0) >= 1) {
        return `${head}\n${body.replace(/^\s*[-*•] /, "- ").replace(/ [-•] (?=\S)/g, "\n- ")}`;
      }
      return block;
    })
    .join("\n\n")
    .trim();
}

// ---- Rendering -------------------------------------------------------------------------------------

export type Inline =
  | { t: "text"; v: string }
  | { t: "strong" | "em" | "del"; c: Inline[] }
  | { t: "code"; v: string }
  | { t: "link"; href: string; c: Inline[] }
  | { t: "br" };

export type Block =
  | { t: "p"; c: Inline[] }
  | { t: "h"; c: Inline[] }
  | { t: "ul" | "ol"; items: Inline[][] }
  | { t: "quote"; c: Inline[] }
  | { t: "pre"; v: string }
  | { t: "hr" };

const listRx = /^\s*(?:([-*+•])|(\d{1,3})[.)])\s+(.*)$|^\s*-(?=[^\s-])(?!.*-\s*$)(.*)$/;

/**
 * Markdown to a small block tree. Single line breaks count: the sources write one paragraph per line,
 * so each line becomes its own paragraph. A short line ending in ":" ("Features:") reads as a heading.
 */
export function parseMarkdown(md?: string | null): Block[] {
  const lines = (md ?? "").replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  // Reads past the end as an empty line, so the loops below need no bounds checks on the text.
  const at = (k: number) => lines[k] ?? "";
  for (let i = 0; i < lines.length; i++) {
    const line = at(i);
    if (!line.trim()) continue;
    if (/^ {4}|^\t/.test(line) || /^```/.test(line)) {
      const fenced = /^```/.test(line);
      const buf: string[] = [];
      if (fenced) {
        for (i++; i < lines.length && !/^```/.test(at(i)); i++) buf.push(at(i));
      } else {
        for (; i < lines.length && (/^ {4}|^\t/.test(at(i)) || (!at(i).trim() && /^ {4}|^\t/.test(at(i + 1)))); i++) buf.push(at(i).replace(/^ {4}|^\t/, ""));
        i--;
      }
      out.push({ t: "pre", v: buf.join("\n") });
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      out.push({ t: "hr" });
      continue;
    }
    const h = line.match(/^\s*#{1,6}\s+(.*?)\s*#*\s*$/);
    if (h) {
      out.push({ t: "h", c: parseInline(h[1]) });
      continue;
    }
    if (/^\s*>/.test(line)) {
      const buf: string[] = [];
      for (; i < lines.length && /^\s*>/.test(at(i)); i++) buf.push(at(i).replace(/^\s*>\s?/, ""));
      i--;
      out.push({ t: "quote", c: parseInline(buf.join("\n")) });
      continue;
    }
    const li = line.match(listRx);
    if (li) {
      const ordered = !!li[2];
      const items: Inline[][] = [];
      for (; i < lines.length; i++) {
        const m = at(i).match(listRx);
        if (m && !!m[2] === ordered) items.push(parseInline(m[3] ?? m[4]));
        else if (at(i).trim() && /^\s{2,}\S/.test(at(i)) && items.length) items.at(-1)?.push({ t: "br" }, ...parseInline(at(i).trim()));
        else break;
      }
      i--;
      out.push({ t: ordered ? "ol" : "ul", items });
      continue;
    }
    const text = line.trim();
    const bare = text.replace(/^\*\*(.*)\*\*$/, "$1");
    if (bare.length <= 60 && /:\**$/.test(text) && !/https?:\/\//.test(text)) out.push({ t: "h", c: parseInline(bare) });
    else out.push({ t: "p", c: parseInline(text) });
  }
  return out;
}

const safeHref = (u = "") => (/^(https?:|mailto:)/i.test(u) ? u : /^www\./i.test(u) ? `https://${u}` : null);

/** Inline Markdown: **bold**, *em*, ~~del~~, `code`, [text](url), and bare URLs. */
export function parseInline(src = ""): Inline[] {
  const out: Inline[] = [];
  let text = "";
  const flush = () => {
    if (text) out.push({ t: "text", v: text });
    text = "";
  };
  let i = 0;
  while (i < src.length) {
    const rest = src.slice(i);
    let m: RegExpMatchArray | null;
    if (src[i] === "\n") {
      flush();
      out.push({ t: "br" });
      i++;
    } else if (src[i] === "\\" && /[\\`*_[\]()#+\-.!~>]/.test(src[i + 1] ?? "")) {
      text += src[i + 1];
      i += 2;
    } else if ((m = rest.match(/^`([^`]+)`/))) {
      flush();
      out.push({ t: "code", v: m[1] ?? "" });
      i += m[0].length;
    } else if ((m = rest.match(/^\[([^\]]+)\]\(\s*<?([^)\s>]+)>?\s*\)/)) && safeHref(m[2])) {
      flush();
      out.push({ t: "link", href: safeHref(m[2])!, c: parseInline(m[1]) });
      i += m[0].length;
    } else if ((m = rest.match(/^(\*\*|__)(?=\S)([\s\S]*?\S)\1/))) {
      flush();
      out.push({ t: "strong", c: parseInline(m[2]) });
      i += m[0].length;
    } else if ((m = rest.match(/^~~(?=\S)([\s\S]*?\S)~~/))) {
      flush();
      out.push({ t: "del", c: parseInline(m[1]) });
      i += m[0].length;
    } else if ((m = rest.match(/^\*(?=[^\s*])([^*\n]*?[^\s*])\*(?!\*)/)) || (!/\w/.test(src[i - 1] ?? "") && (m = rest.match(/^_(?=[^\s_])([^_\n]*?[^\s_])_(?!\w)/)))) {
      flush();
      out.push({ t: "em", c: parseInline(m[1]) });
      i += m[0].length;
    } else if (!/[\w/]/.test(src[i - 1] ?? "") && (m = rest.match(/^(?:https?:\/\/|www\.)[^\s<>()]*[^\s<>().,;:!?'"]/i))) {
      flush();
      out.push({ t: "link", href: safeHref(m[0])!, c: [{ t: "text", v: m[0] }] });
      i += m[0].length;
    } else {
      text += src[i];
      i++;
    }
  }
  flush();
  return out;
}

const inlineText = (c: Inline[]): string =>
  c.map((n) => (n.t === "text" || n.t === "code" ? n.v : n.t === "br" ? " " : inlineText(n.c))).join("");

/** Markdown (or BBCode) as one line of plain text, for summaries, cards and gamelist.xml. */
export function toPlainText(md?: string | null): string {
  return parseMarkdown(bbcodeToMarkdown(md))
    .map((b) => (b.t === "pre" ? b.v : b.t === "hr" ? "" : "items" in b ? b.items.map(inlineText).join("; ") : inlineText(b.c)))
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Roughly how long the description reads, so long ones can start collapsed. */
export const blockLength = (blocks: Block[]) =>
  blocks.reduce((n, b) => n + 40 + (b.t === "pre" ? b.v.length : b.t === "hr" ? 0 : "items" in b ? b.items.reduce((k, it) => k + 30 + inlineText(it).length, 0) : inlineText(b.c).length), 0);
