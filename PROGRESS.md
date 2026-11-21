## p-laser, on p-econ2 8a44af7
Root cause: our laser fights are held at whatever range the fight starts, and we stand exposed while nothing can fire. House guns hit 0.90 at 30-60 m but 0.12 past 160 m; the laser is hitscan to 280 m.

Change (taken from p-fight 0cfc542, without the gun-duel kiter or the lightning plan):
- laser_plan(): a laser holder against a target with no laser and no lightning keeps a 150-195 m band (kite_max capped at vision - 5). Boost on back-off.
- While no held weapon can fire (laser cooling, gun reloading), duck behind a prism within 20 m, do a tactical reload out of sight, then return to the firing spot.
- Fight sub-modes are exempt from the I3 mode commit.
- Gun aim was checked: lead() already solves the intercept at the config gun speed (252) and advances one tick. No change.

Gates:
- scen_fire prism gun+laser 2.33 -> 2.88 (+24%); open gun+laser 3.06 -> 3.18; gun only unchanged (1.20 / 1.80).
- smoke PASS. docker --cpus 1 --memory 512m read-only, network none: 0 late, PASS.
- 192-seed sim (seed0 1000, 5 procs, dmg_mult 0.6 defend_r 40 retaliate t_all 400 ramp_dt 60): econ2 3.46, plaser 3.49; paired +0.03 +- 0.23 (31 better / 30 worse).
