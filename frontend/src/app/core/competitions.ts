/** Competiciones que sigue Bets AI: nombre corto y orden fijo en filtros y listas. */

const COMPETITIONS: { key: string; short: string }[] = [
  { key: 'soccer_spain_la_liga', short: 'LaLiga' },
  { key: 'soccer_epl', short: 'Premier League' },
  { key: 'soccer_italy_serie_a', short: 'Serie A' },
  { key: 'soccer_germany_bundesliga', short: 'Bundesliga' },
  { key: 'soccer_france_ligue_one', short: 'Ligue 1' },
  { key: 'soccer_uefa_champs_league', short: 'Champions League' },
  { key: 'soccer_uefa_europa_league', short: 'Europa League' },
  { key: 'soccer_uefa_europa_conference_league', short: 'Conference League' },
];

const ORDER = new Map(COMPETITIONS.map((c, i) => [c.key, i]));
const SHORT = new Map(COMPETITIONS.map((c) => [c.key, c.short]));

export function competitionName(sportKey: string, fallback: string): string {
  return SHORT.get(sportKey) ?? fallback;
}

export function competitionOrder(sportKey: string): number {
  return ORDER.get(sportKey) ?? COMPETITIONS.length;
}
