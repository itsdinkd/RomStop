// Roll a Rom Hack: which hacks the randomizer may land on.
import type {
  RomshelfHackSummary,
  RomshelfMatchLevel,
} from "@/services/api/romshelf";

const PATCHABLE: RomshelfMatchLevel[] = ["exact", "likely"];

/** Not installed yet, RomStop can fetch its patch, and the library has a ROM to patch it onto. */
export function rollable(
  hacks: RomshelfHackSummary[],
  exceptId?: string,
): RomshelfHackSummary[] {
  return hacks.filter(
    (h) =>
      h.id !== exceptId &&
      !h.installs.length &&
      h.downloadable &&
      PATCHABLE.includes(h.match),
  );
}

/** Case-opening rarity colour, from the hack's rating (like the coloured strip under each item). */
export function rarity(h: RomshelfHackSummary): string {
  const s = h.rating?.score ?? 0;
  if (s >= 4.6) return "gold";
  if (s >= 4.2) return "red";
  if (s >= 3.8) return "pink";
  if (s > 0) return "purple";
  return "blue";
}

export function pickRandom<T>(list: T[]): T {
  return list[Math.floor(Math.random() * list.length)]!;
}

/** Remembers the last list the index page loaded, so rolling from it needs no second request. */
let lastHacks: RomshelfHackSummary[] | null = null;
export function rememberHacks(hacks: RomshelfHackSummary[]) {
  lastHacks = hacks;
}
export function rememberedHacks() {
  return lastHacks;
}
