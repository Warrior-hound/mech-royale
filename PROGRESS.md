## p-fight (I2 + research card 2/3 kiter), on p-econ 84ff12a
Root cause: in a fight our bot holds its spot in the open and jinks, with no range choice by matchup and no use of cover.

Change: fight_plan() in bot.py.
- Lightning closes in only on a weak target or a laser holder.
- A laser against a target with no laser and no lightning keeps a 150-195 m band.
- Otherwise we hold.
- Kiter, for every plan: while no weapon can fire, go to the nearest straight-walkable spot within 20 m where a prism hides the whole body (else back off 20 m). Do a tactical reload once out of sight, then return to the last firing spot. Boost on duck, return and kite-out.
- Fight sub-modes are exempt from the p-econ I3 mode commit.

Measurements:

| version | scen_fire prism 60/100 m, gun | prism, gun+laser | open, gun | open, gun+laser | 192-seed sim (7000+) |
|---|---|---|---|---|---|
| v1, on v5.2 | | | 1.79 (v5.2: 1.81) | 3.19 (v5.2: 3.11) | -0.19 ±0.29 vs v52b |
| kiter, on p-econ | 1.30 (econ: 1.20) | 2.88 (econ: 2.33) | 1.76 (econ: 1.80) | 3.18 (econ: 3.06) | -0.12 ±0.33 vs econ; sim dealt/taken 2.16 -> 2.27 |

- The "avoid fights" variant (engage_k 0.8, detail_hp_ratio 0.8) scored -0.57 ±0.35 vs v52b.
- smoke PASS. docker --cpus 1 --memory 512m: 0 late.
- The DONE WHEN target (+30% scen_fire) is not met. In gun duels, about 80% of the damage we take comes while we are exposed and firing.
