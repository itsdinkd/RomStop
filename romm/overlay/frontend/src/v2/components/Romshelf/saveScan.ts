// Save import, browser side: read the folders the user picked and collect
// what the planner needs. Files are only read here; nothing on the device is
// changed, and only save, state and state-screenshot files are ever uploaded.
// The server (handler/romshelf/saves.py) decides what each file is.
import SparkMD5 from "spark-md5";

/** A file from a picked folder, with its path from the folder's name down. */
export interface PickedFile {
  path: string;
  file: File;
}

export interface ScanResult {
  /** Save, state and state-screenshot files, by path. */
  saves: Map<string, File>;
  /** Other files, the ones that may be ROMs, by path. */
  others: PickedFile[];
  playlists: string[];
  gamelists: { path: string; text: string }[];
  esSystems: string[];
}

// Kept loose: anything the server might call a save comes along, the rest of
// a folder (ROMs, BIOS, configs) is only listed by name.
const SAVE_FILE =
  /\.(srm|sav|sa1|sra|eep|fla|mpk|rtc|dsv|mcr|mcd|bkr|brm|bcr|nv|ram|dat|gci|ps2|raw|state\d*(\.auto)?|ss\d|p2s|mln|ppst|sstate|dst|ds\d|bin)$/i;
const STATE_SCREENSHOT = /\.state\d*(\.auto)?\.png$/i;
const ARCHIVE = /\.(zip|7z|rar|chd|iso|cso|rvz|wbfs|nsp|xci|pbp)$/i;
const MAX_CONFIG_BYTES = 4 * 1024 * 1024;
/** ROMs bigger than this are matched by name only. */
export const MAX_ROM_HASH_BYTES = 64 * 1024 * 1024;

/** The files of an `<input webkitdirectory>` pick. */
export function pickedFiles(list: FileList | null): PickedFile[] {
  return Array.from(list ?? []).map((file) => ({
    path: (file.webkitRelativePath || file.name).replace(/\\/g, "/"),
    file,
  }));
}

/** `bin` is a save only inside a saves or memory-card folder (it is also a disc image). */
function isSaveFile(path: string): boolean {
  if (STATE_SCREENSHOT.test(path)) return true;
  if (!SAVE_FILE.test(path)) return false;
  if (/\.bin$/i.test(path)) return /\/(savedata|saves?)\//i.test(path);
  return true;
}

export async function scanFolders(files: PickedFile[]): Promise<ScanResult> {
  const result: ScanResult = {
    saves: new Map(),
    others: [],
    playlists: [],
    gamelists: [],
    esSystems: [],
  };
  for (const f of files) {
    const lower = f.path.toLowerCase();
    const name = lower.split("/").pop() ?? "";
    if (name.startsWith(".")) continue;
    if (f.file.size <= MAX_CONFIG_BYTES) {
      if (name.endsWith(".lpl")) {
        result.playlists.push(await f.file.text());
        continue;
      }
      if (name === "gamelist.xml") {
        result.gamelists.push({ path: f.path, text: await f.file.text() });
        continue;
      }
      if (name === "es_systems.xml") {
        result.esSystems.push(await f.file.text());
        continue;
      }
    }
    if (isSaveFile(lower)) result.saves.set(f.path, f.file);
    else result.others.push(f);
  }
  return result;
}

export function md5Of(file: Blob): Promise<string> {
  return file.arrayBuffer().then((buf) => SparkMD5.ArrayBuffer.hash(buf));
}

let crcTable: Uint32Array | null = null;

export async function crc32Of(file: Blob): Promise<string> {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c >>> 0;
    }
  }
  const table = crcTable;
  const bytes = new Uint8Array(await file.arrayBuffer());
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) {
    crc = table[(crc ^ bytes[i]!) & 0xff]! ^ (crc >>> 8);
  }
  return ((crc ^ 0xffffffff) >>> 0).toString(16).padStart(8, "0");
}

/** The game name a save or state file is named after (`Game.state1` → `game`). */
export function saveStem(path: string): string {
  const name = path.split("/").pop() ?? path;
  return name
    .replace(/\.png$/i, "")
    .replace(/\.state\d*(\.auto)?$/i, "")
    .replace(/\.[^.]+$/, "")
    .replace(/_\d$/, "")
    .toLowerCase();
}

function stemOf(path: string): string {
  const name = path.split("/").pop() ?? path;
  return name.replace(/\.[^.]+$/, "").toLowerCase();
}

/**
 * Device files that may be the ROMs the saves belong to: same name as a save.
 * Only their paths go to the server, plus a CRC32 for `hashStems` (saves
 * that did not match by name) when the ROM is small and not an archive.
 */
export async function deviceRoms(
  others: PickedFile[],
  saveStems: Set<string>,
  hashStems: Set<string> = new Set(),
): Promise<{ path: string; crc32?: string | undefined }[]> {
  const out: { path: string; crc32?: string | undefined }[] = [];
  for (const f of others) {
    const stem = stemOf(f.path);
    if (!saveStems.has(stem)) continue;
    let crc32: string | undefined;
    if (
      hashStems.has(stem) &&
      f.file.size <= MAX_ROM_HASH_BYTES &&
      !ARCHIVE.test(f.path)
    ) {
      crc32 = await crc32Of(f.file);
    }
    out.push({ path: f.path, crc32 });
  }
  return out;
}
