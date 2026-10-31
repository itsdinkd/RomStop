// SMW Central's difficulty scales for "Mario Hack Specifics". A copy of the
// scales in the RomStop site's src/lib/mario.ts; keep the two in step.
import type { RomshelfHack } from "@/services/api/romshelf";

export interface DifficultyLevel {
  label: string;
  color: string;
  description: string;
}

/** SMW Central's Super Mario World scale, easiest first, with its own descriptions. */
const SMW: DifficultyLevel[] = [
  { label: "Newcomer", color: "#4b5bd6", description: "Comparable to the easier parts of Super Mario World. Approachable for people who don't play many games." },
  { label: "Casual", color: "#3b8fd9", description: "Comparable to fully completing Super Mario World or other official Mario titles." },
  { label: "Intermediate", color: "#1f9a4a", description: "Players should be competent playing Super Mario World or other platformers." },
  { label: "Advanced", color: "#b48a12", description: "Players should have experience playing hacks, and be proficient at controlling Mario." },
  { label: "Expert", color: "#cf5a1d", description: "Players should be very experienced with playing hacks. Requires exceptional play and persistence." },
  { label: "Master", color: "#c52a2a", description: "Unreasonably demanding for most players while requiring significant effort from highly skilled players." },
  { label: "Grandmaster", color: "#a3335f", description: "Demands perfect play under immense pressure. Sections may take highly skilled players many hours to beat." },
];

/** SMW Central's Super Mario 64 scale. SMW Central gives no descriptions for it; these are RomStop's own. */
const SM64: DifficultyLevel[] = [
  { label: "Easy", color: "#3b8fd9", description: "Gentle challenges. A good fit if you've finished Super Mario 64 and are new to its hacks." },
  { label: "Intermediate", color: "#1f9a4a", description: "Around or a step above Super Mario 64's harder stars. Some hack experience helps." },
  { label: "Hard", color: "#cf5a1d", description: "Demanding platforming for players experienced with Super Mario 64 hacks." },
  { label: "Kaizo", color: "#c52a2a", description: "Precise, punishing challenges built for expert players. Expect many retries." },
];

export function difficultyScale(hack: RomshelfHack): DifficultyLevel[] {
  return hack.console === "n64" ? SM64 : SMW;
}

export function difficultyLevel(hack: RomshelfHack): DifficultyLevel | undefined {
  const d = hack.mario?.difficulty?.toLowerCase();
  return d ? difficultyScale(hack).find((l) => l.label.toLowerCase() === d) : undefined;
}
