"""Fictional clubs and players. No real clubs, players or trademarks are used."""

from __future__ import annotations

import random

from matchmind.schema import Player, Team

# (position, base_x, base_y) in an attacking-right frame, 4-3-3.
FORMATION_433 = [
    ("GK", 5.0, 34.0),
    ("RB", 32.0, 8.0),
    ("CB", 24.0, 25.0),
    ("CB", 24.0, 43.0),
    ("LB", 32.0, 60.0),
    ("CM", 46.0, 20.0),
    ("DM", 40.0, 34.0),
    ("CM", 46.0, 48.0),
    ("RW", 74.0, 10.0),
    ("ST", 84.0, 34.0),
    ("LW", 74.0, 58.0),
]
BENCH = [("GK", 5.0, 34.0), ("CB", 24.0, 34.0), ("CM", 46.0, 34.0), ("RW", 74.0, 12.0), ("ST", 84.0, 34.0)]

FIRST = [
    "Amari", "Luca", "Kenji", "Mateo", "Idris", "Noah", "Tariq", "Elias", "Rafael", "Yusuf",
    "Oskar", "Jonah", "Kofi", "Dario", "Arjun", "Leon", "Malik", "Tomas", "Sami", "Felix",
    "Hugo", "Ravi", "Emeka", "Nico", "Bruno", "Aksel", "Diego", "Ilan", "Theo", "Zane",
    "Marek", "Kai",
]
LAST = [
    "Okafor", "Lindqvist", "Moreau", "Haddad", "Castellanos", "Brennan", "Sato", "Varga",
    "Achterberg", "Mensah", "Rourke", "Delacroix", "Petrovic", "Nakamura", "Quintero", "Holloway",
    "Abara", "Strand", "Ferreira", "Kowal", "Duarte", "Iqbal", "Marchetti", "Osei", "Lund",
    "Bakker", "Rahimi", "Silvestre", "Novak", "Adeyemi", "Calloway", "Tanaka",
]

CLUBS = [
    ("NBR", "Northbridge Rovers", "#c8102e"),
    ("HCF", "Harbor City FC", "#1d4ed8"),
    ("VAL", "Valemont Athletic", "#15803d"),
    ("KSU", "Kestrel Sound United", "#7c3aed"),
]

_POS_SKILL = {
    "GK": {"passing": 0.6, "shooting": 0.1, "pace": 0.4, "defending": 0.5},
    "CB": {"passing": 0.65, "shooting": 0.3, "pace": 0.55, "defending": 0.85},
    "RB": {"passing": 0.7, "shooting": 0.35, "pace": 0.75, "defending": 0.72},
    "LB": {"passing": 0.7, "shooting": 0.35, "pace": 0.75, "defending": 0.72},
    "DM": {"passing": 0.8, "shooting": 0.45, "pace": 0.6, "defending": 0.78},
    "CM": {"passing": 0.82, "shooting": 0.55, "pace": 0.65, "defending": 0.62},
    "RW": {"passing": 0.75, "shooting": 0.7, "pace": 0.88, "defending": 0.4},
    "LW": {"passing": 0.75, "shooting": 0.7, "pace": 0.88, "defending": 0.4},
    "ST": {"passing": 0.68, "shooting": 0.85, "pace": 0.8, "defending": 0.35},
}


def build_team(club_index: int, seed: int) -> Team:
    short, name, color = CLUBS[club_index % len(CLUBS)]
    rng = random.Random(f"{short}-{seed}")
    firsts, lasts = rng.sample(FIRST, 16), rng.sample(LAST, 16)
    numbers = [1, 2, 4, 5, 3, 8, 6, 10, 7, 9, 11, 13, 15, 14, 17, 19]
    players: list[Player] = []
    for i, (pos, bx, by) in enumerate(FORMATION_433 + BENCH):
        skill = _POS_SKILL[pos]
        jitter = lambda v: round(min(0.97, max(0.25, v + rng.uniform(-0.12, 0.12))), 2)
        players.append(
            Player(
                id=f"{short}-{numbers[i]}",
                name=f"{firsts[i]} {lasts[i]}",
                number=numbers[i],
                position=pos,
                team_id=short,
                base_x=bx,
                base_y=by,
                passing=jitter(skill["passing"]),
                shooting=jitter(skill["shooting"]),
                pace=jitter(skill["pace"]),
                defending=jitter(skill["defending"]),
                stamina=jitter(0.75),
                starter=i < 11,
            )
        )
    return Team(id=short, name=name, short=short, color=color, players=players)
