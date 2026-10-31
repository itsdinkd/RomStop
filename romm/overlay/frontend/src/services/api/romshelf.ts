// RomStop: romhacks for the library's ROMs, from the RomStop index.
// Shapes mirror backend/endpoints/romshelf.py.
import api from "@/services/api";

export type RomshelfMatchLevel = "exact" | "likely" | "wrong_version" | "none";

export interface RomshelfBaseRom {
  title?: string;
  region?: string;
  revision?: string;
  header?: string;
  crc32?: string;
  sha1?: string;
  /** No-Intro checksums of the dump without a header (lowercase hex). */
  headerlessCrc32?: string;
  headerlessSha1?: string;
  noIntroName?: string;
  notes?: string;
}

export interface RomshelfVersion {
  version: string;
  date?: string;
  changelog?: string[];
  patchFormat?: string;
  baseRom?: RomshelfBaseRom;
  url?: string;
}

export interface RomshelfRating {
  source: string;
  score: number;
  display?: string;
  count: number;
  url?: string;
}

export interface RomshelfCompatibility {
  works?: string[];
  only?: boolean;
  issues?: string[];
  broken?: string[];
  hardware?: "works" | "issues" | "broken";
  notes?: string[];
  sources?: string[];
  summary?: string;
  /** Emulator id -> display name. */
  names?: Record<string, string>;
}

export interface RomshelfPlayPick<T = string> {
  use: T[];
  emulators: string[];
  ok: boolean;
  pinned: boolean;
}

export interface RomshelfHack {
  id: string;
  title: string;
  console: string;
  baseGame: string;
  baseRom?: RomshelfBaseRom;
  authors: string[];
  type: string;
  genre?: string;
  version?: string;
  versions: RomshelfVersion[];
  released?: string;
  updated?: string;
  summary: string;
  description: string;
  features?: string[];
  tags?: string[];
  rating?: { score: number; count: number; sample?: boolean };
  ratings: RomshelfRating[];
  /** Total Favs: Romhacking.net reviews that recommend the hack. */
  favs?: number;
  favsUrl?: string;
  links: { label: string; url: string }[];
  screenshots: string[];
  pageUrl?: string;
  downloadUrl?: string;
  patchFormat?: string;
  /** Formats of the patches RomStop lists, e.g. ["BPS", "IPS"]. */
  patchFormats?: string[];
  /** Versions RomStop has a patch for. */
  patchVersions?: string[];
  /** Download count at the source site. */
  downloads?: number;
  language?: string;
  compatibility?: RomshelfCompatibility;
  /** Wii / Vita mod packages: installed by hand, never patched by RomM. */
  install?: RomshelfInstallGuide;
  /** Where to download a mod package from (RomStop's mirror, else the source). */
  installUrl?: string | null;
  /** Mario Hack Specifics: SMW Central's Difficulty, Length and Type (only hacks from SMW Central). */
  mario?: { difficulty?: string; length?: string; types?: string[] };
  play?: { emulatorjs?: RomshelfPlayPick; esde?: RomshelfPlayPick; batocera?: RomshelfPlayPick<{ emulator: string; core: string }> };
}

export interface RomshelfInstallGuide {
  method: string;
  label: string;
  needs: string;
  steps: string[];
  emulator?: string;
  /** The hack page's own install notes (Markdown). */
  notes?: string;
  guideUrl?: string;
}

export interface RomshelfInstall {
  id: string;
  hack_id: string;
  version: string | null;
  rom_id: number | null;
  status: "pending" | "done";
  fs_name: string;
  base_rom_id: number;
  installed_at: string;
  checksum_validated: boolean;
}

export interface RomshelfLibraryFile {
  id: number;
  file_name: string;
  size: number;
  crc32: string | null;
  sha1: string | null;
  headerless_crc32: string | null;
  headerless_sha1: string | null;
}

export interface RomshelfLibraryRom {
  id: number;
  name: string;
  fs_name: string;
  platform_id: number;
  platform_slug: string;
  regions: string[];
  revision: string | null;
  files: RomshelfLibraryFile[];
}

export interface RomshelfMatch {
  level: RomshelfMatchLevel;
  reason: string;
  rom_id: number;
  file_id: number | null;
  strip_header: boolean;
  rom?: RomshelfLibraryRom;
  versions?: Record<string, RomshelfMatchLevel>;
}

export interface RomshelfStatus {
  index_url: string;
  site_url: string;
  fetched_at: number | null;
  generated: string | null;
  hack_count: number;
  error: string | null;
  // Library ROMs still being checked for copier/iNES headers.
  hashing?: number;
}

export interface RomshelfRomHack {
  hack: RomshelfHack;
  needed: string;
  match: RomshelfMatch;
  versions: Record<string, RomshelfMatchLevel>;
  installs: RomshelfInstall[];
}

export interface RomshelfRomResponse {
  rom: RomshelfLibraryRom | null;
  supported: boolean;
  installed_from: RomshelfInstall | null;
  installed_hack: {
    id: string;
    title: string;
    version?: string;
    pageUrl?: string;
    baseGame: string;
  } | null;
  hacks: RomshelfRomHack[];
  status: RomshelfStatus;
}

export interface RomshelfHackSummary {
  id: string;
  title: string;
  console: string;
  baseGame: string;
  baseRom?: RomshelfBaseRom;
  authors: string[];
  type: string;
  genre?: string;
  version?: string;
  released?: string;
  updated?: string;
  summary: string;
  rating?: { score: number; count: number; sample?: boolean };
  /** Total Favs: Romhacking.net reviews that recommend the hack. */
  favs?: number;
  tags?: string[];
  pageUrl?: string;
  downloads?: number;
  cover: string | null;
  versionCount: number;
  downloadable: boolean;
  match: RomshelfMatchLevel;
  matches: { rom_id: number; level: RomshelfMatchLevel }[];
  installs: RomshelfInstall[];
  /** The signed-in user saved this hack (Save Rom Hack). */
  saved: boolean;
}

export interface RomshelfConsole {
  name: string;
  short: string;
  extensions: string[];
  colors?: [string, string];
}

export interface RomshelfListResponse {
  status: RomshelfStatus;
  consoles: Record<string, RomshelfConsole>;
  platform_ids: number[];
  hacks: RomshelfHackSummary[];
}

export interface RomshelfHackResponse {
  hack: RomshelfHack;
  needed: string;
  matches: RomshelfMatch[];
  installs: RomshelfInstall[];
  saved: boolean;
}

export interface RomshelfInstallInput {
  hackId: string;
  romId: number;
  fileId?: number | null;
  version?: string | null;
  patchMember?: string | null;
  replace?: boolean;
  patchFile?: File | null;
}

export type RomshelfInstallResult = RomshelfInstall & {
  needed: string;
  match: RomshelfMatchLevel;
};

/** What the backend's install errors carry, so the UI can offer the next step. */
export interface RomshelfInstallError {
  message: string;
  code:
    | "browser_download"
    | "choose_patch"
    | "choose_file"
    | "already_installed"
    | "patch_failed"
    | string;
  candidates?: string[];
}

// RomStop console id -> RomM platform slug, for platform icons.
export const ROMSHELF_CONSOLE_SLUGS: Record<string, string> = {
  nes: "nes",
  snes: "snes",
  n64: "n64",
  gc: "ngc",
  gb: "gb",
  gbc: "gbc",
  gba: "gba",
  nds: "nds",
  vb: "virtualboy",
  sms: "sms",
  genesis: "genesis",
  segacd: "segacd",
  "32x": "sega32",
  saturn: "saturn",
  dreamcast: "dc",
  gg: "gamegear",
  pce: "tg16",
  neogeo: "neogeoaes",
  psx: "psx",
  ps2: "ps2",
  psp: "psp",
  wii: "wii",
  vita: "psvita",
  atari2600: "atari2600",
  lynx: "lynx",
  msx: "msx",
  arcade: "arcade",
};

async function getRomHacks(romId: number) {
  return api.get<RomshelfRomResponse>(`/romshelf/roms/${romId}`);
}

async function getHacks() {
  return api.get<RomshelfListResponse>("/romshelf/hacks");
}

async function getHack(hackId: string) {
  return api.get<RomshelfHackResponse>(
    `/romshelf/hacks/${encodeURIComponent(hackId)}`,
  );
}

/** This RomM's installs (downloads) and play sessions of a hack. */
export interface RomshelfCounts {
  downloads: number;
  plays: number;
}

async function getCounts(hackId: string) {
  return api.get<{ counts: RomshelfCounts }>(
    `/romshelf/counts/${encodeURIComponent(hackId)}`,
  );
}

/** Save Rom Hack: bookmarks kept on the RomM server, per user. */
async function setSaved(hackId: string, saved: boolean) {
  const url = `/romshelf/saved/${encodeURIComponent(hackId)}`;
  return saved
    ? api.put<{ id: string; saved: boolean }>(url)
    : api.delete<{ id: string; saved: boolean }>(url);
}

async function getInstall(recordId: string) {
  return api.get<RomshelfInstall>(`/romshelf/installs/${recordId}`);
}

async function refreshIndex() {
  return api.post<RomshelfStatus>("/romshelf/refresh");
}

async function install(input: RomshelfInstallInput) {
  const form = new FormData();
  form.append("hack_id", input.hackId);
  form.append("rom_id", String(input.romId));
  if (input.fileId) form.append("file_id", String(input.fileId));
  if (input.version) form.append("version", input.version);
  if (input.patchMember) form.append("patch_member", input.patchMember);
  if (input.replace) form.append("replace", "true");
  if (input.patchFile) form.append("patch_file", input.patchFile);
  return api.post<RomshelfInstallResult>("/romshelf/install", form);
}

// ---- Save import. Shapes mirror the /romshelf/saves routes.

export interface SaveImportGame {
  id: number;
  name: string;
  fs_name: string;
  fs_name_no_ext: string;
  platform_id: number;
  platform_slug: string;
  platform_name: string;
}

export interface SaveImportOptions {
  user: string;
  platforms: { id: number; name: string; slug: string; roms: number }[];
  games: SaveImportGame[];
  emulators: string[];
}

export type SaveImportKind =
  | "save"
  | "state"
  | "shared_card"
  | "folder_save";

export type SaveImportStatus =
  | "new"
  | "in_romm"
  | "conflict"
  | "unmatched"
  | "ambiguous"
  | "unsupported";

export interface SaveImportRow {
  path: string;
  name: string;
  kind: SaveImportKind;
  size: number;
  status: SaveImportStatus;
  reason: string | null;
  match: "exact" | "name" | "checksum" | "similar" | "chosen" | null;
  rom: SaveImportGame | null;
  candidates: SaveImportGame[];
  emulator: string | null;
  emulator_source: string | null;
  rename: boolean;
  /** The ROM's own name for this file, when it differs from the file's. */
  renamed_name?: string;
  target_name: string;
  /** For a conflict: the versioned name the file is kept under next to RomM's. */
  keep_both_name: string | null;
  screenshot_path: string | null;
}

export interface SaveImportOverride {
  rom_id?: number;
  emulator?: string;
  rename?: boolean;
  skip_emulator?: boolean;
}

export interface SaveImportPlanInput {
  files: { path: string; size: number; md5: string; modified: number }[];
  device_roms: { path: string; crc32?: string | undefined }[];
  playlists: string[];
  gamelists: { path: string; text: string }[];
  es_systems: string[];
  platform_id: number | null;
  detect_emulator: boolean;
  platform_emulators: Record<number, string>;
  overrides: Record<string, SaveImportOverride>;
}

async function getSaveImportOptions() {
  return api.get<SaveImportOptions>("/romshelf/saves/options");
}

async function planSaveImport(input: SaveImportPlanInput) {
  return api.post<{ user: string; rows: SaveImportRow[] }>(
    "/romshelf/saves/plan",
    input,
  );
}

/** Upload through RomM's own save/state routes, never with `overwrite`. */
async function uploadSaveFile(input: {
  kind: "save" | "state";
  romId: number;
  emulator: string | null;
  file: Blob;
  fileName: string;
  screenshot?: Blob | undefined;
  screenshotName?: string | undefined;
}) {
  const form = new FormData();
  const field = input.kind === "save" ? "saveFile" : "stateFile";
  form.append(field, input.file, input.fileName);
  if (input.screenshot && input.screenshotName) {
    form.append("screenshotFile", input.screenshot, input.screenshotName);
  }
  const params: Record<string, string | number> = { rom_id: input.romId };
  if (input.emulator) params.emulator = input.emulator;
  return api.post(input.kind === "save" ? "/saves" : "/states", form, {
    params,
    headers: { "Content-Type": "multipart/form-data" },
  });
}

/** The structured detail of a failed install, or a plain message. */
// Same "top rated" order as the RomStop site: a few 5-star votes do not
// outrank hundreds of 4.6s.
export function rankScore(
  rating: { score: number; count: number } | null | undefined,
  prior = 3.5,
  weight = 25,
): number {
  const score = rating?.score ?? 0;
  const count = rating?.score ? rating.count : 0;
  return (score * count + prior * weight) / (count + weight);
}

export function installErrorOf(err: unknown): RomshelfInstallError {
  const anyErr = err as {
    response?: { data?: { detail?: unknown } };
    message?: string;
  };
  const detail = anyErr?.response?.data?.detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    return detail as RomshelfInstallError;
  }
  if (typeof detail === "string") return { message: detail, code: "error" };
  return { message: anyErr?.message ?? String(err), code: "error" };
}

export default {
  getRomHacks,
  getHacks,
  getHack,
  getCounts,
  setSaved,
  getInstall,
  refreshIndex,
  install,
  getSaveImportOptions,
  planSaveImport,
  uploadSaveFile,
};
