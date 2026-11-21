## p-fight (I2), based on v5.2-base 7e03de5
Root cause: in a fight our bot holds its spot in the open and jinks, with no range choice by matchup and no shooting from cover.
Change: fight_plan(). Laser vs a target without a laser: kite at 150-195 m. Gun vs gun: peek from a cover edge within 36 m, else standoff. Lightning: close in only on a weak target or a laser holder. Each plan is kept for 1.5 s. Boost on kite-out.
scen_fire dealt/taken (v5.2-base -> p-fight):
- it1: gun 1.81->1.78, gun+laser 3.11->2.95
- it2 (36 m peek): gun 1.68
- it3 (+boost, standoff): gun 1.79, gun+laser 3.19
smoke PASS. docker 0 late.
192-seed sim (seeds 7000+): .cache/builders/sim_runs/pfight.jsonl (also has the disengage variant engage_k 0.8, detail_hp_ratio 0.8).
Next: Lead kill decision (budget: one more iteration, used).
