// What a hack's pages say about emulators, and which EmulatorJS core RomM's
// player should use for an installed hack. The RomStop index works out the
// candidates; this picks the first one RomM offers for the platform.
import { userStorage } from "@/composables/useUserLocalStorage";
import type { RomshelfHack } from "@/services/api/romshelf";
import { getSupportedEJSCores } from "@/utils";

export interface BrowserCore {
  /** Core to play with, or null for RomM's own choice. */
  core: string | null;
  /** False when the hack's pages rule out every core RomM offers here. */
  ok: boolean;
}

export function browserCoreFor(
  hack: RomshelfHack,
  platformSlug: string,
): BrowserCore {
  const pick = hack.play?.emulatorjs;
  if (!pick?.pinned) return { core: null, ok: true };
  const offered = getSupportedEJSCores(platformSlug);
  const core = pick.use.find((c) => offered.includes(c)) ?? null;
  return { core, ok: core !== null };
}

/**
 * Remember the working core for an installed hack, the same way RomM's player
 * remembers a core the user picked. A core the user already chose is kept.
 */
export function rememberHackCore(
  romId: number,
  platformSlug: string,
  hack: RomshelfHack,
): void {
  const { core } = browserCoreFor(hack, platformSlug);
  const key = `player:${romId}:core`;
  if (core && !userStorage.getItem(key)) userStorage.setItem(key, core);
}

export function hasCompatWarning(hack: RomshelfHack): boolean {
  const c = hack.compatibility;
  return !!c && (!!c.only || !!c.broken?.length || !!c.issues?.length);
}

export const emulatorName = (hack: RomshelfHack, id: string) =>
  hack.compatibility?.names?.[id] ?? id;
