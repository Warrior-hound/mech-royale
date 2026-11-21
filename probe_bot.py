"""Probe bots: score-as-oracle variants of app/bot.py (see state/builders/ENV_REVERSE.md section 3).

One mode per submission, chosen by the PROBE environment variable (baked into the probe's Dockerfile):
  hide   P1 pacifist hider: never fires. Walks to the best-hidden reachable cell (buildings/canopy block
         most sight lines) inside the next circle, stands still (silent), relocates as the zone moves or
         when spotted, and holds the final centre at full HP for the HP race. Plating/armor upgrades.
  kill1  P2 one-kill-then-hide: the full app bot until our first mech kill (or KILL1_DEADLINE_S), then
         exactly the `hide` policy for the rest of the match.
  open   P3 pacifist in the open: like `hide` but ignores the hide score (goes to the zone goal and
         stands there). P1 - P3 = what hiding is worth.
  idle   P0 sitting target: never moves, never fires (only answers offers).
Score = 16 - place; with no kills, a pacifist's score counts the house bots that die to the zone/NPCs
(or the endgame HP race) before it.
"""
from __future__ import annotations

import math
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(os.path.dirname(HERE), "app")):
    if os.path.isfile(os.path.join(_p, "bot.py")) and _p not in sys.path:
        sys.path.insert(0, _p)

import mechfight as mf  # noqa: E402
import bot as basebot  # noqa: E402
from bot import Bot, hyp, wrap  # noqa: E402

MODE = os.environ.get("PROBE", "hide").strip().lower()
KILL1_DEADLINE_S = 180.0      # kill1: switch to hiding by then even without a kill
SILENT_MOVE = 0.40            # |move| 0.40 -> ~4.7 m/s, under the 5 m/s step-sound threshold
HIDE_GRID = 4                 # sample hide candidates every HIDE_GRID nav cells (2 m cells -> 8 m)
HIDE_DIRS = 12                # sight-line directions per candidate
HIDE_RAY = 110.0              # ray length (m); the 200 m wedge beyond this is mostly blocked anyway
SURVIVAL_UPGRADES = ["plating", "armor", "nanites", "reactor", "sensors", "servos", "capacitor", "jets", "gyros"]


class ProbeBot(Bot):
    name = "probe-" + MODE

    def __init__(self, hello: Dict[str, Any]) -> None:
        super().__init__(hello)
        self.cands: List[Tuple[float, float, float, int]] = []  # x, y, hide score 0..1, component
        self.comp = None
        self.goal: Optional[Tuple[float, float]] = None
        self.goal_t = -99.0
        self.goal_zone = None
        self.bad: Dict[Tuple[int, int], float] = {}
        self.seen: Dict[int, Tuple[float, float, float]] = {}  # enemy id -> x, y, t
        self.spotted_t = -99.0
        self.hiding = MODE in ("hide", "open")
        self.killed = 0
        self.prog_pos: Optional[Tuple[float, float, float]] = None
        zph = (self.cfg.get("zone") or {}).get("phases") or []
        tt, self.phase_starts = 0.0, []
        for ph in zph:
            self.phase_starts.append(tt)
            tt += ph.get("hold_s", 0) + ph.get("shrink_s", 0)
        self.zone_end_t = tt
        self.final_t = self.phase_starts[-2] if len(self.phase_starts) >= 2 else 1e9  # last two phases
        if MODE in ("hide", "kill1", "open") and self.world is not None:
            t0 = time.perf_counter()
            try:
                self._components()
                self._hide_map(score=(MODE != "open"))
            except Exception as e:  # never crash at hello
                mf.log("hide map failed", repr(e))
                self.cands = []
            mf.log(f"probe {MODE}: {len(self.cands)} candidates in {time.perf_counter() - t0:.2f}s")

    # ------------------------------------------------------------------ offline-at-hello precompute
    def _components(self) -> None:
        """Label nav cells by walk-connectivity (same neighbour/rise rules as world.AStar)."""
        W = self.world
        nx, n, nav, floor, rise = W.nx, W.nx * W.ny, W.nav, W.floor, W.rise
        comp = [-1] * n
        cid = 0
        nb = (1, -1, nx, -nx)
        for s in range(n):
            if not nav[s] or comp[s] >= 0:
                continue
            comp[s] = cid
            stack = [s]
            while stack:
                c = stack.pop()
                fc = floor[c]
                for d in nb:
                    k = c + d
                    if 0 <= k < n and nav[k] and comp[k] < 0 and abs(floor[k] - fc) <= rise:
                        comp[k] = cid
                        stack.append(k)
            cid += 1
        self.comp = comp

    def _hide_map(self, score: bool) -> None:
        """Hide score per sampled cell: share of directions whose level sight line at body height is
        blocked by a building (any distance) or by > 4 m of body-height canopy beyond near range."""
        W = self.world
        nx, ny, occ, canopy, ground, nav = W.nx, W.ny, W.occ, W.canopy, W.ground, W.nav
        cell = basebot.worldmod.CELL
        dirs = [(math.cos(2 * math.pi * k / HIDE_DIRS), math.sin(2 * math.pi * k / HIDE_DIRS)) for k in range(HIDE_DIRS)]
        steps = int(HIDE_RAY / cell)
        near_steps = int(45.0 / cell)
        out = []
        for j in range(2, ny - 2, HIDE_GRID):
            for i in range(2, nx - 2, HIDE_GRID):
                c = j * nx + i
                if not nav[c]:
                    continue
                if not score:
                    out.append((W.x0 + (i + 0.5) * cell, W.y0 + (j + 0.5) * cell, 0.0, self.comp[c]))
                    continue
                top = ground[c] + 9.5
                blocked = 0.0
                for (dx, dy) in dirs:
                    crown = 0.0
                    hit = 0.0
                    for s in range(1, steps):
                        ii = int(i + dx * s)
                        jj = int(j + dy * s)
                        if ii < 0 or jj < 0 or ii >= nx or jj >= ny:
                            hit = 1.0
                            break
                        k = jj * nx + ii
                        if occ[k] > top:
                            hit = 1.0
                            break
                        if canopy[k] > 0:
                            crown += cell
                            if crown > 4.0:
                                hit = 0.6 if s > near_steps else 0.35  # canopy never stops near range
                                break
                    blocked += hit
                out.append((W.x0 + (i + 0.5) * cell, W.y0 + (j + 0.5) * cell, blocked / HIDE_DIRS, self.comp[c]))
        self.cands = out

    # ------------------------------------------------------------------ decisions
    def _act(self, obs: Dict[str, Any], t0: float) -> Dict[str, Any]:
        if MODE == "idle":
            return self._idle(obs)
        if MODE == "kill1" and not self.hiding:
            for e in obs.get("events") or []:
                if e.get("type") == "kill" and isinstance(e.get("target"), int) and e["target"] < 100:
                    self.killed += 1
            if self.killed >= 1 or obs.get("t", 0) >= KILL1_DEADLINE_S:
                self.hiding = True
                self.goal = None
                mf.log(f"kill1: switching to hide at t={obs.get('t')} kills={self.killed}")
            else:
                return super()._act(obs, t0)
        return self._hide(obs)

    def _pick_offer(self, obs, act) -> None:
        off = obs.get("offer")
        if off and off.get("choices"):
            best, bi = 999, 0
            for i, ch in enumerate(off["choices"]):
                cid = ch.get("id")
                r = SURVIVAL_UPGRADES.index(cid) if cid in SURVIVAL_UPGRADES else 50
                if r < best:
                    best, bi = r, i
            act["pick"] = bi

    def _idle(self, obs) -> Dict[str, Any]:
        act: Dict[str, Any] = {"move": [0.0, 0.0], "fire": [False, False]}
        self._pick_offer(obs, act)
        return act

    def _choose_goal(self, obs, x, y, t) -> Tuple[float, float]:
        z = obs["zone"]
        ncx, ncy = z["next_center"]
        nr = z["next_radius"]
        if t >= self.final_t or nr <= 30:
            return (ncx, ncy)  # HP race: the final centre stays inside longest
        margin = 10.0 + 0.2 * nr
        avail = max(5.0, z["shrink_end"] - t) if z["shrink_end"] > t else max(5.0, z["shrink_start"] - t + 30)
        reach = self.walk * avail / 1.3
        mycomp = None
        if self.comp is not None and self.world is not None:
            c0 = self.world.nearest_nav(x, y)
            mycomp = self.comp[c0] if c0 is not None else None
        foes = [(fx, fy) for (fx, fy, ft) in self.seen.values() if t - ft < 20.0]
        best, bs = None, -1e9
        for (cx, cy, sc, cc) in self.cands:
            dz = hyp(cx, cy, ncx, ncy)
            if dz > nr - margin:
                continue
            if mycomp is not None and cc != mycomp:
                continue
            key = (int(cx), int(cy))
            if self.bad.get(key, -99) > t - 30:
                continue
            d = hyp(x, y, cx, cy)
            if d > reach:
                continue
            s = 3.0 * sc - d / 150.0 - 0.3 * dz / max(1.0, nr)
            for (fx, fy) in foes:
                df = hyp(cx, cy, fx, fy)
                if df < 90:
                    s -= (90 - df) / 30.0
            if MODE == "open":
                s = -d / 150.0 - dz / max(1.0, nr)
            if s > bs:
                bs, best = s, (cx, cy)
        if best is None:
            gx, gy, _ = self.zone_goal(obs, x, y)
            return (gx, gy)
        return best

    def _hide(self, obs) -> Dict[str, Any]:
        me = obs["self"]
        t = obs["t"]
        x, y, z = me["pos"]
        act: Dict[str, Any] = {"fire": [False, False]}
        self._pick_offer(obs, act)
        damaged = False
        for e in obs.get("events") or []:
            if e.get("type") == "damaged" and e.get("weapon") != "zone":
                damaged = True
                self.last_damage_t = t
                if e.get("bearing") is not None:
                    self.dmg_bearing = e["bearing"]
        enemies = [m for m in obs.get("mechs") or [] if m.get("kind") == "mech" and m.get("id") != me.get("id")]
        for m in enemies:
            self.seen[m["id"]] = (m["pos"][0], m["pos"][1], t)
        spotted = damaged or any(hyp(x, y, m["pos"][0], m["pos"][1]) < 150 for m in enemies)
        zone = obs["zone"]
        zkey = (round(zone["next_center"][0]), round(zone["next_center"][1]), round(zone["next_radius"]))
        replan = (self.goal is None or zkey != self.goal_zone or t - self.goal_t > 15.0
                  or (spotted and t - self.spotted_t > 4.0))
        if spotted:
            self.spotted_t = t if replan else self.spotted_t
        if replan:
            if self.goal is not None and spotted:
                self.bad[(int(self.goal[0]), int(self.goal[1]))] = t  # this spot is burnt
            self.goal = self._choose_goal(obs, x, y, t)
            self.goal_t = t
            self.goal_zone = zkey
            self.prog_pos = (x, y, t)
        gx, gy = self.goal
        d = hyp(x, y, gx, gy)
        mv = [0.0, 0.0]
        if d > 2.0:
            mv = self.steer(x, y, (gx, gy), t, me)
            # stuck or unreachable (steer asked for a jet climb): burn the spot and replan next tick
            if self.climb_until > t or (self.prog_pos and t - self.prog_pos[2] > 4.0):
                if self.prog_pos and hyp(x, y, self.prog_pos[0], self.prog_pos[1]) < 2.0 or self.climb_until > t:
                    self.bad[(int(gx), int(gy))] = t
                    self.goal = None
                    self.climb_until = -1.0
                self.prog_pos = (x, y, t)
            # speed: silent unless the zone timing or being hunted needs full walk
            in_zone = me.get("in_zone", True)
            need = d * 1.3 / max(1.0, zone["shrink_end"] - t) if zone["shrink_end"] > t else 0.0
            hurry = (not in_zone) or spotted or t - self.last_damage_t < 6.0 or need > 4.0 or t >= self.final_t
            sc = 1.0 if hurry else SILENT_MOVE
            mv = [mv[0] * sc, mv[1] * sc]
            act["boost"] = (not in_zone) or (t - self.last_damage_t < 2.0)
        act["move"] = mv
        # torso: face the nearest known hunter (rear hits x1.5), else damage bearing, else sweep slowly
        foes = [(fx, fy) for (fx, fy, ft) in self.seen.values() if t - ft < 8.0]
        if foes:
            fx, fy = min(foes, key=lambda p: hyp(x, y, p[0], p[1]))
            yaw = math.atan2(fy - y, fx - x)
        elif t - self.last_damage_t < 5.0 and self.dmg_bearing is not None:
            yaw = self.dmg_bearing
        else:
            yaw = wrap(self.last_aim[0] + 0.25)
        act["aim"] = [yaw, 0.0]
        self.last_aim = list(act["aim"])
        return act


if __name__ == "__main__":
    try:
        mf.run(ProbeBot)
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os._exit(0)
