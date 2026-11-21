"""Survival-first mech bot (mechfight/1).

Priorities every decision (0.1 s):
 1. stay out of the zone: walk early toward the nearest edge of the *next* circle (inset);
 2. stay unseen: pick hiding spots whose line of sight to known enemies is blocked by buildings,
    or that sit deep in tree canopy, walk slowly (silent < 5 m/s) when nothing is urgent;
 3. loot weapons / cubes only when nothing hostile has been seen recently;
 4. fight back when attacked or when a target is weak, with lead for bullets and shells;
 5. never crash: any error falls back to a safe action (keep moving to the zone, keep aim).
"""
from __future__ import annotations

import math
import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

import mechfight as mf

try:
    import world as worldmod
except Exception:  # pragma: no cover - world module is shipped next to the bot
    worldmod = None

TAU = 2 * math.pi
WEAPON_RANK = {"laser": 3, "lightning": 2, "mortar": 1, "gun": 0}
UPGRADE_PRIORITY = ["nanites", "plating", "armor", "reactor", "sensors", "servos", "laser_damage",
                    "gun_damage", "gun_rate", "laser_charge", "capacitor", "gyros", "jets",
                    "lightning_range", "lightning_efficiency", "lightning_chain", "gun_mag",
                    "gun_velocity", "mortar_splash", "mortar_reload", "mortar_velocity", "laser_pierce"]
SOUND_DIST = {"near": 40.0, "mid": 110.0, "far": 230.0}
HOSTILE_SOUNDS = {"step", "boost", "jet", "gun", "laser_charge", "laser", "lightning", "mortar", "reload"}


def wrap(a: float) -> float:
    return (a + math.pi) % TAU - math.pi


def hyp(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)


def find_map_dir(hello: Dict[str, Any]) -> Optional[str]:
    d = (hello.get("map") or {}).get("dir") or "/arena"
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (d, os.path.join(os.getcwd(), d), os.path.join(here, d), "/arena",
                 os.path.join(here, "..", "arena"), os.path.join(here, "arena")):
        try:
            if os.path.isfile(os.path.join(cand, "v2", "world.json")):
                return cand
        except Exception:
            pass
    return None


class Bot:
    name = "survivor-v1"

    def __init__(self, hello: Dict[str, Any]) -> None:
        cfg = hello.get("config") or {}
        self.cfg = cfg
        self.me_id = hello.get("slot")
        w = cfg.get("weapons") or {}
        self.gun_speed = (w.get("gun") or {}).get("speed", 252.0)
        self.mortar_speed = (w.get("mortar") or {}).get("speed", 72.0)
        self.mortar_g = (w.get("mortar") or {}).get("gravity", 22.0)
        self.light_range = (w.get("lightning") or {}).get("range", 45.0)
        self.light_cone = math.radians((w.get("lightning") or {}).get("cone_deg", 30.0))
        self.laser_charge = (w.get("laser") or {}).get("charge", 0.75)
        self.reach = (cfg.get("loot") or {}).get("pickup_radius", 4.0)
        self.near_range = (cfg.get("vision") or {}).get("near_range", 45.0)
        self.world = None
        t0 = time.time()
        try:
            md = find_map_dir(hello)
            if md and worldmod is not None:
                mech = cfg.get("mech") or {}
                self.world = worldmod.World(md, mech.get("step_up", 1.2), mech.get("height", 9.0))
            mf.log(f"world loaded from {md} in {time.time() - t0:.2f}s")
        except Exception:
            mf.log("world load failed:\n" + traceback.format_exc())
            self.world = None
        # memory
        self.last_damage_t = -99.0
        self.last_hostile_t = -99.0
        self.dmg_bearing: Optional[float] = None
        self.dmg_by: Optional[float] = None
        self.attackers: Dict[int, float] = {}
        self.pseudo: List[Tuple[float, float, float, float]] = []  # x, y, weight, t
        self.planner = None
        self.plan_goal: Optional[Tuple[float, float]] = None
        self.plan_t = -99.0
        self.path: Optional[List[Tuple[float, float]]] = None
        self.path_i = 0
        self.cover: Optional[Tuple[float, float]] = None
        self.cover_t = -99.0
        self.cover_score = -1e9
        self.stuck_n = 0
        self.unstick_until = -1.0
        self.unstick_dir = 1.0
        self.prev_pos: Optional[List[float]] = None
        self.last_aim = [0.0, 0.0]
        self.scan_i = 0
        self.scan_t = 0.0
        self.taken_fail: Dict[int, float] = {}
        self.mode = "start"
        self.slow_ticks = 0
        self.tick_t0 = 0.0
        self.cover_budget_s = 0.004
        self.path_partial = False
        self.climb_until = -1.0  # wall-time cap for the cover search inside one decision

    # ------------------------------------------------------------------ main entry
    def act(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        t_start = time.perf_counter()
        self.tick_t0 = t_start
        try:
            a = self._act(obs, t_start)
        except Exception:
            mf.log("act error:\n" + traceback.format_exc())
            a = self._safe(obs)
        dt = (time.perf_counter() - t_start) * 1000
        if dt > 20:
            self.slow_ticks += 1
        return a

    def _safe(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            me = obs["self"]
            x, y = me["pos"][0], me["pos"][1]
            z = obs["zone"]
            gx, gy = z["next_center"]
            d = hyp(x, y, gx, gy)
            mv = [(gx - x) / d, (gy - y) / d] if d > 1 else [0.0, 0.0]
            return {"move": mv, "aim": list(self.last_aim), "boost": not me.get("in_zone", True)}
        except Exception:
            return {"aim": list(self.last_aim)}

    # ------------------------------------------------------------------ helpers
    def zone_radius_at(self, zone: Dict[str, Any], t_now: float, t_future: float) -> Tuple[float, float, float]:
        cx, cy = zone["center"]
        r = zone["radius"]
        nx, ny = zone["next_center"]
        nr = zone["next_radius"]
        s0, s1 = zone["shrink_start"], zone["shrink_end"]
        if t_future <= s0 or s1 <= t_now:
            return cx, cy, r
        start = max(t_now, s0)
        f = min(1.0, (t_future - start) / max(1e-3, s1 - start))
        return cx + (nx - cx) * f, cy + (ny - cy) * f, r + (nr - r) * f

    def zone_goal(self, obs: Dict[str, Any], x: float, y: float) -> Tuple[float, float, float]:
        """Point in the next circle closest to us (inset), and the inset radius."""
        z = obs["zone"]
        ncx, ncy = z["next_center"]
        nr = z["next_radius"]
        inset = max(0.0, nr - min(30.0, 0.35 * nr + 3.0))
        d = hyp(ncx, ncy, x, y)
        if d <= inset:
            return x, y, inset
        f = inset / d
        return ncx + (x - ncx) * f, ncy + (y - ncy) * f, inset

    def safe_point(self, obs: Dict[str, Any], px: float, py: float, t: float) -> float:
        """Penalty for standing at p in the near future (zone)."""
        z = obs["zone"]
        pen = 0.0
        cx, cy, r = self.zone_radius_at(z, t, t + 12.0)
        dd = hyp(cx, cy, px, py)
        if dd > r - 4:
            pen += 25.0 + (dd - r) * 0.5
        ncx, ncy = z["next_center"]
        if hyp(ncx, ncy, px, py) > z["next_radius"] - 5 and z["shrink_start"] - t < 25:
            pen += 8.0
        return pen

    # ------------------------------------------------------------------ the decision
    def _act(self, obs: Dict[str, Any], t0: float) -> Dict[str, Any]:
        me = obs["self"]
        t = obs["t"]
        x, y, z = me["pos"]
        vx, vy = me["vel"][0], me["vel"][1]
        hp, mhp = me["hp"], max(1.0, me["max_hp"])
        stats = me.get("stats") or {}
        eye_h = stats.get("eye_height", 8.0)
        muz_h = stats.get("muzzle_height", 7.0)
        act: Dict[str, Any] = {}
        W = self.world

        # ---- events
        for e in obs.get("events") or []:
            et = e.get("type")
            if et == "damaged":
                if e.get("weapon") != "zone":
                    self.last_damage_t = t
                    self.last_hostile_t = t
                    if e.get("bearing") is not None:
                        self.dmg_bearing = e["bearing"]
                    if e.get("by") is not None:
                        self.attackers[int(e["by"])] = t
                        self.dmg_by = e["by"]
                        if e["by"] < 100 and e.get("bearing") is not None:
                            b = e["bearing"]
                            self.pseudo.append((x + 80 * math.cos(b), y + 80 * math.sin(b), 1.5, t))
            elif et == "take_failed":
                self.taken_fail[int(e.get("id", -1))] = t
        for s in obs.get("sounds") or []:
            if s.get("kind") in HOSTILE_SOUNDS and s.get("age", 0) < 1.0:
                dd = SOUND_DIST.get(s.get("dist"), 110.0)
                b = s["bearing"]
                self.pseudo.append((x + dd * math.cos(b), y + dd * math.sin(b), 0.6, t))
                if dd < 120:
                    self.last_hostile_t = max(self.last_hostile_t, t - 3.0)
        self.pseudo = [p for p in self.pseudo if t - p[3] < 6.0][-12:]

        # ---- offer
        off = obs.get("offer")
        if off and off.get("choices"):
            ids = [c.get("id") for c in off["choices"]]
            best, bi = 999, 0
            for i, cid in enumerate(ids):
                r = UPGRADE_PRIORITY.index(cid) if cid in UPGRADE_PRIORITY else 50
                if r < best:
                    best, bi = r, i
            act["pick"] = bi

        # ---- threats
        enemies = [m for m in obs.get("mechs") or [] if m.get("kind") == "mech" and m.get("id") != me.get("id")]
        npcs = [m for m in obs.get("mechs") or [] if m.get("kind") == "npc"]
        for m in enemies:
            dm = hyp(x, y, m["pos"][0], m["pos"][1])
            if dm < 230:
                self.last_hostile_t = t
        enemies.sort(key=lambda m: hyp(x, y, m["pos"][0], m["pos"][1]))
        threats: List[Tuple[float, float, float, float]] = []  # x, y, z, weight
        for m in enemies[:4]:
            threats.append((m["pos"][0], m["pos"][1], m["pos"][2], 2.0))
        for n in npcs[:3]:
            if hyp(x, y, n["pos"][0], n["pos"][1]) < 90:
                threats.append((n["pos"][0], n["pos"][1], n["pos"][2], 0.7))
        for (px, py, wgt, _) in self.pseudo[-4:]:
            gz = W.ground_at(px, py) if W else z
            threats.append((px, py, gz, wgt))

        engaged = (t - self.last_damage_t) < 4.0
        recent_hostile = (t - self.last_hostile_t) < 12.0

        # ---- target selection (things in our line of sight)
        target = None
        tscore = -1e9
        my_eye = (x, y, z + eye_h)
        for m in enemies[:5] + npcs[:3]:
            mx, my_, mz = m["pos"]
            dm = math.dist((x, y, z), (mx, my_, mz))
            if dm > 240:
                continue
            if W and W.sight_blocked((x, y, z + muz_h), (mx, my_, mz + 4.0)):
                continue
            is_mech = m.get("kind") == "mech"
            frac = (m.get("hp", 1.0) / max(1.0, m.get("max_hp", 1.0))) if "hp" in m else 1.0
            attacker = (t - self.attackers.get(int(m["id"]), -99)) < 5.0
            sc = -dm / 40.0 + (3.0 if attacker else 0.0) + (2.5 * (1 - frac)) + (1.0 if is_mech else -2.0)
            # engage rules: attackers, close mechs, weak mechs; npcs only when they attack us
            want = attacker or (is_mech and (dm < 70 or (frac < 0.35 and dm < 150 and hp / mhp > 0.4)))
            if not is_mech and not attacker:
                want = False
            if want and sc > tscore:
                tscore, target = sc, m

        # ---- movement goal
        zgx, zgy, zinset = self.zone_goal(obs, x, y)
        zdist = hyp(x, y, zgx, zgy)
        zone = obs["zone"]
        time_left = max(1.0, zone["shrink_end"] - t - 8.0)
        cx, cy, cr = zone["center"][0], zone["center"][1], zone["radius"]
        out_now = not me.get("in_zone", True) or hyp(cx, cy, x, y) > cr
        need_speed = zdist / time_left
        urgent = out_now or need_speed > 3.5 or (zone["shrink_start"] - t < 15 and zdist > 5)

        speed = 0.4  # silent walk
        goal: Optional[Tuple[float, float]] = None
        boost = False
        mode = "idle"

        elapsed = (time.perf_counter() - t0) * 1000
        hide_needed = engaged or (enemies and hyp(x, y, enemies[0]["pos"][0], enemies[0]["pos"][1]) < 200) \
            or (hp < mhp * 0.98 and recent_hostile)
        if W and threats and hide_needed and elapsed < 6:
            if t - self.cover_t > 0.5 or self.cover is None:
                self.choose_cover(obs, x, y, z, threats, t)
        if out_now or (urgent and zdist > 8):
            goal = (zgx, zgy)
            speed = 1.0
            mode = "zone"
            if out_now:
                boost = me.get("energy", 0) > 20
            if hide_needed and self.cover is not None and self.safe_point(obs, *self.cover, t) < 1 and not out_now:
                goal = self.cover
                mode = "zone-cover"
        elif hide_needed and self.cover is not None:
            goal = self.cover
            mode = "hide"
            speed = 1.0 if (engaged or (enemies and hyp(x, y, enemies[0]["pos"][0], enemies[0]["pos"][1]) < 90)) else 0.6
            if engaged and hp < mhp * 0.5 and me.get("energy", 0) > 40 and hyp(x, y, *goal) > 10:
                boost = True
        else:
            # safe: loot if close, otherwise go/stay in a hidden spot in the zone
            lg = None if recent_hostile else self.loot_goal(obs, me, x, y, z, act)
            if lg is not None:
                goal = lg
                mode = "loot"
                speed = 0.6
            elif zdist > 3:
                goal = (zgx, zgy)
                mode = "travel"
                speed = 0.4 if need_speed < 2.5 else 1.0
            else:
                # camp: hidden from the likely direction of others (zone centre)
                if W and (self.cover is None or t - self.cover_t > 4.0) and elapsed < 6:
                    ncx, ncy = zone["next_center"]
                    b = math.atan2(ncy - y, ncx - x)
                    pseudo = [(x + 120 * math.cos(b + k), y + 120 * math.sin(b + k), z, 0.5) for k in (-0.6, 0.0, 0.6)]
                    self.choose_cover(obs, x, y, z, pseudo + threats, t, radius=24.0)
                if self.cover is not None and self.safe_point(obs, *self.cover, t) < 1:
                    goal = self.cover
                    mode = "camp"
                    speed = 0.4

        mv = [0.0, 0.0]
        if goal is not None:
            mv = self.steer(x, y, goal, t, me)
            mvn = math.hypot(*mv)
            dgoal = hyp(x, y, goal[0], goal[1])
            if dgoal < 2.0:
                mv = [0.0, 0.0]
            elif mvn > 0:
                sp = speed if dgoal > 4 else min(speed, 0.4)
                mv = [mv[0] / mvn * sp, mv[1] / mvn * sp]

        # ---- stuck detection / unstick
        jump = False
        hs = math.hypot(vx, vy)
        cmd = math.hypot(*mv)
        if cmd > 0.3 and me.get("grounded", True) and hs < 1.2:
            self.stuck_n += 1
        else:
            self.stuck_n = max(0, self.stuck_n - 1)
        if self.stuck_n >= 8 and t > self.unstick_until + 1.0:
            self.unstick_until = t + 1.2
            self.unstick_dir = -self.unstick_dir
            self.stuck_n = 0
            self.planner = None
            self.path = None
            self.plan_t = -99
        if t < self.climb_until and cmd > 0:
            if me.get("grounded", True):
                jump = True
            boost = me.get("energy", 0) > 10
        elif t < self.unstick_until and cmd > 0:
            if me.get("grounded", True):
                jump = True
            boost = me.get("energy", 0) > 15
            # sidestep a little while climbing
            px, py = -mv[1], mv[0]
            mv = [mv[0] + 0.5 * self.unstick_dir * px, mv[1] + 0.5 * self.unstick_dir * py]
            n = math.hypot(*mv) or 1.0
            mv = [mv[0] / n, mv[1] / n]

        # ---- aim and fire
        fire = [False, False]
        weapons = me.get("weapons") or [None, None]
        aim = None
        if target is not None:
            aim, fire = self.fire_control(me, target, weapons, x, y, z, muz_h)
            if mode in ("travel", "loot", "camp", "idle"):
                mode = "fight"
        if aim is None:
            aim = self.scan_aim(obs, x, y, t, mv, threats)
        else:
            # laser charging slows us; fine. keep aim
            pass
        # reload when nothing to shoot
        g0 = weapons[0] if weapons else None
        if g0 and g0.get("kind") == "gun" and target is None and g0.get("ammo", 12) < 6 and not g0.get("reload"):
            act["reload"] = True

        self.last_aim = aim
        mn = math.hypot(*mv)
        if mn > 0.998:
            mv = [mv[0] * 0.998 / mn, mv[1] * 0.998 / mn]
        act.update(move=[round(mv[0], 3), round(mv[1], 3)], aim=[round(aim[0], 4), round(aim[1], 4)],
                   fire=fire, boost=bool(boost), jump=bool(jump))
        self.mode = mode
        mf.draw.label(f"{mode} hp{hp:.0f} e{len(enemies)}")
        return act

    # ------------------------------------------------------------------ loot
    def loot_goal(self, obs, me, x, y, z, act) -> Optional[Tuple[float, float]]:
        weapons = me.get("weapons") or [None, None]
        held = [w.get("kind") for w in weapons if w]
        slot1 = weapons[1] if len(weapons) > 1 else None
        best = None
        bd = 1e9
        for p in obs.get("pickups") or []:
            kind = p.get("kind")
            pid = int(p.get("id", -1))
            if kind in held or kind == "gun":
                continue
            if obs["t"] - self.taken_fail.get(pid, -99) < 3.0:
                continue
            better = slot1 is None or WEAPON_RANK.get(kind, 0) > WEAPON_RANK.get(slot1.get("kind"), 0)
            if not better:
                continue
            px, py, pz = p["pos"]
            d = hyp(x, y, px, py)
            if abs(pz - z) > 6 and d < 10:
                continue  # on a roof we can't easily reach
            if d < self.reach - 0.3 and abs(pz - z) < 4:
                act["take"] = p["id"]
                if slot1 is not None:
                    if slot1.get("kind") == "laser" and slot1.get("charge"):
                        act.pop("take", None)
                    else:
                        act["drop"] = 1
                return None
            if d < 90 and d < bd and abs(pz - z) < 5 and self.safe_point(obs, px, py, obs["t"]) < 1:
                bd, best = d, (px, py)
        for c in obs.get("cubes") or []:
            cxp, cyp, czp = c["pos"]
            d = hyp(x, y, cxp, cyp)
            if d < 60 and d < bd and abs(czp - z) < 4 and self.safe_point(obs, cxp, cyp, obs["t"]) < 1:
                bd, best = d, (cxp, cyp)
        return best

    # ------------------------------------------------------------------ cover
    def choose_cover(self, obs, x, y, z, threats, t, radius: float = 32.0) -> None:
        W = self.world
        best = None
        bs = -1e9
        cands = [(x, y)]
        rot = 0.26 if int(t * 2) % 2 else 0.0
        for r in (radius * 0.3, radius * 0.6, radius):
            for k in range(12):
                a = k * TAU / 12 + rot
                cands.append((x + r * math.cos(a), y + r * math.sin(a)))
        ths = sorted(threats, key=lambda th: hyp(x, y, th[0], th[1]) / th[3])[:3]
        deadline = self.tick_t0 + self.cover_budget_s
        for (px, py) in cands:
            if time.perf_counter() > deadline:
                break
            if not W.walkable(px, py):
                continue
            dme = hyp(x, y, px, py)
            if dme > 3 and not W.line_clear(x, y, px, py):
                # allow if reachable through path finding later: small penalty only for short hops
                if dme > radius * 0.35:
                    continue
            gz = W.ground_at(px, py)
            sc = -dme / 12.0 - self.safe_point(obs, px, py, t)
            can = W.canopy_at(px, py)
            for (tx, ty, tz, wgt) in ths:
                dt = hyp(tx, ty, px, py)
                eye = (tx, ty, tz + 8.0)
                hidden = W.sight_blocked(eye, (px, py, gz + 9.0)) and W.sight_blocked(eye, (px, py, gz + 4.5))
                if hidden:
                    sc += 6.0 * wgt
                elif dt > self.near_range and can >= 4.0:
                    sc += 4.0 * wgt
                elif dt > 200:
                    sc += 2.0 * wgt
                sc += min(dt, 220.0) / 40.0 * wgt
            if (px, py) == (x, y):
                sc += 0.5
            if sc > bs:
                bs, best = sc, (px, py)
        # hysteresis: keep old cover unless clearly better
        if self.cover is not None and best is not None and t - self.cover_t < 3.0:
            ox, oy = self.cover
            if hyp(ox, oy, best[0], best[1]) > 3 and bs < self.cover_score + 1.5:
                self.cover_t = t
                return
        self.cover, self.cover_score, self.cover_t = best, bs, t

    # ------------------------------------------------------------------ steering
    def steer(self, x: float, y: float, goal: Tuple[float, float], t: float, me) -> List[float]:
        W = self.world
        gx, gy = goal
        d = hyp(x, y, gx, gy)
        if d < 0.5:
            return [0.0, 0.0]
        direct = [(gx - x) / d, (gy - y) / d]
        if W is None:
            return direct
        if d < 60 and W.line_clear(x, y, gx, gy):
            self.path = None
            return direct
        # (re)plan
        if self.plan_goal is None or hyp(self.plan_goal[0], self.plan_goal[1], gx, gy) > 6 or \
                (self.path is None and self.planner is None) or t - self.plan_t > 4.0:
            self.planner = worldmod.AStar(W, (x, y), (gx, gy))
            self.plan_goal = (gx, gy)
            self.plan_t = t
        if self.planner is not None:
            if self.planner.step(600, self.tick_t0 + 0.007):
                if self.planner.path:
                    self.path = self.planner.path
                    self.path_i = 0
                    self.path_partial = not self.planner.reached
                self.planner = None
        if self.path and self.path_partial and self.path_i >= len(self.path) - 3 and d > 15:
            # the walkable graph doesn't connect us to the goal: climb/jet over toward it
            self.climb_until = t + 2.5
            self.path = None
            self.plan_t = t - 2.0  # replan soon after the climb
            return direct
        if self.path:
            pts = self.path
            while self.path_i < len(pts) - 1 and hyp(x, y, *pts[self.path_i]) < 3.0:
                self.path_i += 1
            # skip ahead along visible waypoints
            tgt = pts[self.path_i]
            for k in (min(len(pts) - 1, self.path_i + 12), min(len(pts) - 1, self.path_i + 5)):
                if k > self.path_i and W.line_clear(x, y, *pts[k]):
                    tgt = pts[k]
                    self.path_i = max(self.path_i, k - 2)
                    break
            dd = hyp(x, y, *tgt)
            if dd > 0.3:
                return [(tgt[0] - x) / dd, (tgt[1] - y) / dd]
        # local avoidance while planning
        base = math.atan2(direct[1], direct[0])
        for off in (0.0, 0.5, -0.5, 1.0, -1.0, 1.5, -1.5):
            a = base + off
            if W.line_clear(x, y, x + 8 * math.cos(a), y + 8 * math.sin(a)):
                return [math.cos(a), math.sin(a)]
        return direct

    # ------------------------------------------------------------------ aiming
    def scan_aim(self, obs, x, y, t, mv, threats) -> List[float]:
        # face the most recent damage / sound / nearest threat; else sweep around
        if t - self.last_damage_t < 3.0 and self.dmg_bearing is not None:
            return [self.dmg_bearing, 0.0]
        if threats:
            th = min(threats, key=lambda th: hyp(x, y, th[0], th[1]) / th[3])
            return [math.atan2(th[1] - y, th[0] - x), 0.0]
        zc = obs["zone"]["next_center"]
        if hyp(x, y, zc[0], zc[1]) > 5:
            base = math.atan2(zc[1] - y, zc[0] - x)
        elif math.hypot(*mv) > 0.1:
            base = math.atan2(mv[1], mv[0])
        else:
            base = self.last_aim[0]
        if t - self.scan_t > 0.8:
            self.scan_t = t
            self.scan_i = (self.scan_i + 1) % 6
        offs = (0.0, 1.9, -1.9, 0.0, math.pi, 0.9)
        return [wrap(base + offs[self.scan_i]), 0.0]

    def lead(self, sx, sy, sz, tp, tv, speed) -> Tuple[float, float, float, float]:
        px, py, pz = tp
        vx, vy, vz = tv
        tt = 0.0
        for _ in range(3):
            ax, ay, az = px + vx * tt - sx, py + vy * tt - sy, pz + vz * tt * 0.3 - sz
            tt = math.sqrt(ax * ax + ay * ay + az * az) / speed
        return px + vx * tt, py + vy * tt, pz + vz * tt * 0.3, tt

    def fire_control(self, me, m, weapons, x, y, z, muz_h):
        sx, sy, sz = x, y, z + muz_h
        tp = m["pos"]
        tv = m.get("vel") or [0, 0, 0]
        is_npc = m.get("kind") == "npc"
        cz = 1.75 if is_npc else 4.5
        tpos = (tp[0], tp[1], tp[2] + cz)
        d = hyp(x, y, tp[0], tp[1])
        cur = me.get("aim") or self.last_aim
        kinds = [w.get("kind") if w else None for w in weapons]
        fire = [False, False]

        def aim_to(px, py, pz):
            return [math.atan2(py - sy, px - sx), math.atan2(pz - sz, max(0.1, hyp(sx, sy, px, py)))]

        def err(a):
            dy = abs(wrap(a[0] - cur[0]))
            dp = abs(a[1] - cur[1])
            return math.hypot(dy, dp)

        # pick the aim from the best ready weapon
        aim = None
        primary = None
        for slot in (1, 0):
            w = weapons[slot] if slot < len(weapons) else None
            if not w:
                continue
            k = w.get("kind")
            if k == "lightning" and d <= self.light_range - 2 and me.get("energy", 0) > 8:
                primary = slot
                aim = aim_to(*tpos)
                break
            if k == "laser" and d <= 270 and (w.get("cooldown", 0) <= 0.05 or w.get("charge", 0) > 0):
                primary = slot
                aim = aim_to(*tpos)
                break
            if k == "mortar" and 30 < d < 220 and w.get("cooldown", 0) <= 0.05:
                sol = self.mortar_aim(sx, sy, sz, tpos, tv)
                if sol is not None and (not kinds[0] == "gun" or d > 120):
                    primary = slot
                    aim = sol
                    break
        gun_aim = None
        if kinds[0] == "gun" and d < 230:
            lx, ly, lz, _ = self.lead(sx, sy, sz, tpos, tv, self.gun_speed)
            gun_aim = aim_to(lx, ly, lz)
        if aim is None:
            aim = gun_aim
            primary = 0 if gun_aim else None
        if aim is None:
            return None, fire
        tol = math.atan2(2.2 if not is_npc else 1.4, max(1.0, d))
        e = err(aim)
        if primary is not None:
            w = weapons[primary]
            k = w.get("kind")
            if k == "laser":
                ch = w.get("charge", 0) or 0
                if ch < self.laser_charge:
                    fire[primary] = e < 0.35  # build charge while roughly on target
                else:
                    fire[primary] = e > tol  # release (fire) when on target; keep holding otherwise
            elif k == "lightning":
                fire[primary] = abs(wrap(aim[0] - cur[0])) < self.light_cone / 2 - 0.05
            elif k == "mortar":
                fire[primary] = e < 0.04
            else:
                fire[primary] = e < tol
        if gun_aim is not None and primary != 0:
            g = weapons[0]
            if g and (g.get("ammo", 1) > 0) and err(gun_aim) < tol * 1.3:
                fire[0] = True
        return aim, fire

    def mortar_aim(self, sx, sy, sz, tpos, tv) -> Optional[List[float]]:
        v, g = self.mortar_speed, self.mortar_g
        px, py, pz = tpos
        tt = 1.0
        for _ in range(3):
            qx, qy = px + tv[0] * tt, py + tv[1] * tt
            d = hyp(sx, sy, qx, qy)
            dz = pz - 1.0 - (sz + 2.5)
            disc = v ** 4 - g * (g * d * d + 2 * dz * v * v)
            if disc < 0:
                return None
            th = math.atan2(v * v - math.sqrt(disc), g * d)
            if th > 1.25:
                return None
            tt = d / max(1.0, v * math.cos(th))
        return [math.atan2(qy - sy, qx - sx), th]


if __name__ == "__main__":
    try:
        mf.run(Bot)
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os._exit(0)  # exit at once after `end` (don't wait on the stdin reader thread)
