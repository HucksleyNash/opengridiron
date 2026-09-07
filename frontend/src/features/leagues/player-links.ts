import type { Player } from "../../types";

// A draft/forecast row is an identity, not a complete or current projection.
export type PlayerReference = Pick<Player, "name"> & Partial<Player> & { league_name?: string };
export type PlayerMention = { text: string; player?: PlayerReference };

export function playerMatches(reference: PlayerReference, players: PlayerReference[], leagueId?: number) {
  const scope = reference.league_id ?? leagueId;
  return players.filter((player) =>
    (reference.id != null ? player.id === reference.id : player.name.toLocaleLowerCase() === reference.name.toLocaleLowerCase())
    && (scope == null || player.league_id === scope)
    && (!reference.pro_team || player.pro_team === reference.pro_team)
    && (!reference.position || player.position === reference.position),
  );
}

export function createPlayerMatcher(players: PlayerReference[]) {
  const byName = new Map<string, PlayerReference[]>();
  for (const player of players) {
    // Single-word team defenses must not turn ordinary prose into player links.
    if (!player.name.trim().includes(" ")) continue;
    const key = player.name.toLocaleLowerCase();
    byName.set(key, [...(byName.get(key) || []), player]);
  }
  const names = [...byName.keys()].sort((a, b) => b.length - a.length);
  const pattern = names.length ? new RegExp(`(?<![\\p{L}\\p{N}])(${names.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(?![\\p{L}\\p{N}])`, "giu") : null;
  return (text: string, leagueId?: number): PlayerMention[] => {
    if (!pattern) return [{ text }];
    const parts: PlayerMention[] = [];
    let offset = 0;
    for (const match of text.matchAll(pattern)) {
      const index = match.index!;
      if (index > offset) parts.push({ text: text.slice(offset, index) });
      const options = byName.get(match[0].toLocaleLowerCase())!;
      const scoped = leagueId == null ? options : options.filter((player) => player.league_id == null || player.league_id === leagueId);
      const player = scoped.length === 1 ? scoped[0] : { name: match[0], league_id: leagueId };
      parts.push({ text: match[0], player });
      offset = index + match[0].length;
    }
    if (offset < text.length) parts.push({ text: text.slice(offset) });
    return parts;
  };
}

export function isCompletePlayer(player: PlayerReference): player is Player {
  return player.id != null && player.league_id != null && player.ownership != null
    && player.projected_points != null && player.floor != null && player.ceiling != null
    && player.risk != null && player.ros_value !== undefined;
}
