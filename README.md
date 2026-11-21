# survivor-v2: a survival-first mechfight/1 bot

One Python file per concern, standard library only (no pip installs), so the image builds in seconds
and nothing can break at build time.

| File | Role |
| --- | --- |
| `bot.py` | the decision logic (`Bot.act`), run by `mechfight.run` |
| `world.py` | static map model built once at `hello` from `/arena` (about 2 s), plus incremental A* |
| `mechfight.py` | the kit's wrapper, unchanged (stdin/stdout loop, newest-observation rule, debug drawing) |
| `Dockerfile` | `python:3.13.1-slim-bookworm` pinned by digest, runs as uid 65534, writes nothing to disk |

## Map model (`world.py`)

2 m grid over the arena, built from `v2/world.json` and `v2/terrain.i16`:

- **floor**: standing height = terrain, or the top of a building/stadium prism or low bridge deck.
- **wall / nav**: a cell is navigable if it is standable (no overhang inside the body volume) and no
  neighbour rises more than the step height (1.2 m + slack) within one cell (body clearance).
  Moving between cells also requires `floor[next] - floor[here] <= rise`; drops are always allowed.
- **occ**: highest sight/shot blocker per cell (terrain or building top), for line-of-sight rays.
- **canopy**: how deep a cell lies inside tree crowns at body height. Deeper than about 4 m hides a
  mech from viewers beyond the 45 m proximity sensor (crowns block vision, never shots).
- **AStar**: 8-connected A* that runs incrementally (bounded expansions and a wall-clock deadline per
  decision), so path planning never threatens the 50 ms budget.

## Behaviour (`bot.py`), in priority order

1. **Zone first.** The goal is the point of the *next* circle closest to us, inset by up to 30 m, so
   we sit on the edge of the safe area rather than in the contested centre. We start walking there at
   once. Silent pace (|move| = 0.4, below the 5 m/s step-sound threshold) unless the remaining distance
   over the time to the shrink's end needs more; out of the zone we run and boost.
2. **Stay unseen.** When enemies are visible or remembered, after being hit, or when hurt after recent
   contact, the bot scores up to 37 nearby spots: hidden from each known threat (buildings block a 3D
   ray from the threat's eye to both our centre and top), or deep in canopy beyond 45 m, far from
   threats, cheap to reach, and safe from the zone for the next 12 s. Then it walks or boosts there.
   Threats include visible mechs, nearby NPC drones, the bearing of the last hit and recent hostile
   sounds (placed at the bucket distance along the heard bearing). Standing still in cover lets the
   45 HP/s regen run.
3. **Loot only when quiet.** No hostiles seen or heard for 12 s: take weapons (laser > lightning >
   mortar, swapping slot 1 when better) and cubes within 90 m / 60 m that lie inside the safe area.
   Upgrade offers are picked by a survival-first priority list (nanites, plating, armor, reactor, ...).
4. **Fight only when it pays.** Shoot back at anything that hit us in the last 5 s, at mechs within
   70 m, and at weak mechs (< 35% HP) within 150 m while we're above 40% HP, and at NPCs only when they
   attack us. Targets must be in line of sight. Gun shots lead the target (252 m/s intercept) and fire
   only when the actual torso aim is within the target radius. Laser charges while roughly on target
   and releases at full charge when on target. Lightning fires inside 43 m when the cone covers the
   target. Mortar solves the low ballistic arc with lead. The gun reloads when idle and under 6 rounds.
5. **Awareness.** With no target, the torso faces the last hit bearing, else the nearest threat, else
   it sweeps around the direction of the zone centre (front, +/-110 deg, behind) to avoid x1.5 rear hits.
6. **Never stuck, never crash.** No progress for 0.8 s while moving -> jump + jet with a sidestep and a
   replan. If the walkable graph doesn't reach the goal (closed courtyards, roofs, the stadium bowl),
   it walks to the closest reachable point and jets over toward the goal. Any exception in `act` returns
   a safe action (walk to the next circle, keep aim); the wrapper adds a second safety net.

## v2 changes (every number from `hello.config`; free parameters in `PARAMS` at the top of `bot.py`)

- **Fire discipline.** Each gun bullet fires only if its predicted hit probability is at least
  `gun_min_hit_p`: Gaussian spread (sigma = `spread_deg`) plus the displacement of a randomly
  accelerating target over the flight time, against the body cross-section (radius + bullet radius,
  half the height), using the *current* torso aim. Lead is the closed-form intercept
  |D + V t| = s t + 3 (the shot spawns 3 m out), with both ends advanced by one tick. The laser
  releases when the beam's hit probability is at least `laser_min_hit_p`.
- **Engagement gate (Lanchester/TTK).** `their_dps` sums every enemy with line of sight to our body
  (weapons from `mechs[].weapons`, sustained DPS from config, cube multipliers, `enemy_aim_mult`).
  A mech target is fought when `ttk_us < k * ttk_them` (`engage_k`, times `endgame_k_mult` from the
  start of the second-to-last zone phase). Fight = hold ground with jinks, or close to 0.7 x the
  lightning range when holding lightning. Otherwise hide in cover and regen; attackers are still shot
  back opportunistically.
- **Dodges.** Jinks against gun shooters farther than `jink_min_d` = 252 x sqrt(2 (R + r_b) / a),
  about 76 m (closer than that a full swerve during the flight can't clear our radius). The jink
  flips at random intervals of about one bullet flight time. Mortar shells: predict the ballistic
  impact. If our coasting position at impact is inside splash + radius + margin, run away from it
  (boost). Lasers are hitscan and can't be dodged, so a charging laser aimed at us takes the torso
  facing (front arc, no x1.5).
- **Zone.** The rotation is timed on the remaining A* path length (or straight x `detour`) against
  the moment the shrinking edge passes our position. From the second-to-last phase, the goal is the
  circle centre (HP race). Climbs onto roofs or out of courtyards get a jet time from the height
  (rise / 8.9 m/s + 1 s) and trigger beyond 3 m (v1: fixed 2.5 s, beyond 15 m only). Stuck detection
  also fires on "commanded to move, < 1.5 m progress in 1 s".
- **Cover.** 4 rings x 16 bearings out to `cover_radius` (48 m), searched away from the threat first.
  Off-level candidates (roofs) are rejected; detours are charged.
- **Economy.** Lightning > laser > mortar (replay winner: gun + lightning). Upgrades put plating
  first. Drones and crates within `farm_range` are farmed for cubes while it's quiet, HP is at least
  `farm_min_hp`, and the endgame hasn't started.

## Scenario harness (`tools/scenarios.py`, v1 snapshot vs v2)

Deterministic checks from the SPEC physics on the real map, with exact prism/terrain/canopy LOS
from `tools/losmodel.py`. Opponents are scripted, so these numbers compare controllers; they don't
predict scores.

| scenario | v1 | v2 |
| --- | --- | --- |
| A aim, hits/s on a 200 m jinking target (other rows within +/-5%) | 0.23 | 0.45 |
| B under fire from a perfect-lead gunner, 18 spots, 15 s: damage taken / dealt | 1151 / 1005 | 819 / 1152 |
| B rear damage taken | 34 | 11 |
| C mortar lobber, 20 s x 6: damage taken | 1120 | 0 |
| D zone, 10 seeds to 416 s: zone damage / deaths before 415 s | 993 / 3 | 780 / 2 |
| E cover vs one threat: spot hidden (exact LOS) / shot-proof (oracle: 65% have one within 48 m) | 27% / 20% | 43% / 37% |

Run: `.venv/Scripts/python solution/builders/tools/scenarios.py all` (about 30 s).

## Timing

All heavy work is precomputed at `hello`. Per decision, the cover search (5 ms) and A* expansion
(7 ms) are capped by wall-clock time. v2 on the kit stream, in-process: p50 0.13 ms, p99 5.1 ms,
max 5.2 ms. Through `docker run --cpus 1 --memory 512m` on the Windows laptop, warm: v2 p99 9.2-9.4 ms
(v1 5.2 ms on the same run). The first cold run reported p99 25 ms, which is Docker pipe warm-up.

## Check it

```sh
docker build -t survivor-v2 .
python3 check/check.py --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m \
  --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -v "$PWD/arena:/arena:ro" survivor-v2
```

MIT licensed (see `LICENSE`).
