"""Window feature extraction and matching distance."""
import time
from collections import Counter
import zlib

from .settings import FEATURE_NAMES, GRID_DT, MIN_KEYS, MIN_MOVES, NAN, N_BUCKETS

import numpy as np



def _window_end(times, dur):
    """End time of the analysed window: 'now' for live data, else the last event time."""
    now = time.perf_counter()
    if times and abs(now - max(times)) < dur + 5:
        return now
    return max(times) if times else now


def extract_features(moves, buttons, scrolls, keys, dur):
    f = dict.fromkeys(FEATURE_NAMES, NAN)
    stroke_ends = []

    # ---- mouse movement -----------------------------------------------------
    if len(moves) >= 20:
        a = np.asarray(moves, float)
        t, x, y = a[:, 0], a[:, 1], a[:, 2]
        grid = np.arange(t[0], t[-1], GRID_DT)
        if len(grid) >= 20:
            gx, gy = np.interp(grid, t, x), np.interp(grid, t, y)
            idx = np.clip(np.searchsorted(t, grid, side="right"), 1, len(t) - 1)
            still = (t[idx] - t[idx - 1]) > 0.12          # idle gap -> no motion
            dx, dy = np.diff(gx), np.diff(gy)
            dx[still[1:]] = 0
            dy[still[1:]] = 0
            dist = np.hypot(dx, dy)
            speed = dist / GRID_DT
            moving = speed > 30
            f["m_move_ratio"] = float(moving.sum() * GRID_DT / dur)
            if moving.sum() >= 10:
                sp = speed[moving]
                f["m_speed_mean"] = float(sp.mean())
                f["m_speed_std"] = float(sp.std())
                f["m_speed_p50"] = float(np.percentile(sp, 50))
                f["m_speed_p90"] = float(np.percentile(sp, 90))
                f["m_speed_p99"] = float(np.percentile(sp, 99))
                f["m_speed_cv"] = float(sp.std() / max(sp.mean(), 1e-6))

                dv = np.diff(speed) / GRID_DT
                acc = np.abs(dv[moving[1:] & moving[:-1]])
                if len(acc) >= 5:
                    f["m_acc_mean"] = float(acc.mean())
                    f["m_acc_p90"] = float(np.percentile(acc, 90))
                jerk = np.abs(np.diff(dv)) / GRID_DT
                jm = moving[2:] & moving[1:-1] & moving[:-2]
                if jm.sum() >= 5:
                    f["m_jerk_mean"] = float(jerk[jm].mean())

                ang = np.arctan2(dy, dx)
                da = (np.diff(ang) + np.pi) % (2 * np.pi) - np.pi
                tmask = moving[1:] & moving[:-1]
                tm = np.abs(da[tmask])
                if len(tm) >= 5:
                    f["m_turn_mean"] = float(tm.mean())
                    f["m_turn_p90"] = float(np.percentile(tm, 90))
                    f["m_reversal_rate"] = float((tm > 2.4).sum() / dur)
                slow = np.abs(da[tmask & (speed[1:] < 250)])     # slow aiming micro-corrections
                if len(slow) >= 5:
                    f["m_jitter"] = float(slow.mean())

                hist, _ = np.histogram(ang[moving], bins=8, range=(-np.pi, np.pi),
                                       weights=dist[moving])
                if hist.sum() > 0:
                    for i, v in enumerate(hist / hist.sum()):
                        f[f"m_dir{i}"] = float(v)

                ax, ay = np.abs(dx).sum(), np.abs(dy).sum()
                f["m_horiz_ratio"] = float(ax / max(ax + ay, 1e-6))

                # strokes = runs of continuous motion
                m = np.concatenate(([0], moving.astype(np.int8), [0]))
                e = np.diff(m)
                starts, ends = np.where(e == 1)[0], np.where(e == -1)[0]
                straight, lens, durs = [], [], []
                peaks, peakpos, subs, s_list, e_list = [], [], [], [], []
                for s, en in zip(starts, ends):
                    d_s = (en - s) * GRID_DT
                    path = dist[s:en].sum()
                    if d_s < 0.08 or path < 40:
                        continue
                    net = np.hypot(gx[en] - gx[s], gy[en] - gy[s])
                    straight.append(net / path)
                    lens.append(path)
                    durs.append(d_s)
                    seg = speed[s:en]
                    peaks.append(float(seg.max()))
                    peakpos.append(float(seg.argmax()) / max(len(seg) - 1, 1))
                    if len(seg) >= 7:                       # count speed dips = corrective sub-movements
                        sm = np.convolve(seg, np.ones(5) / 5, mode="same")
                        dd = np.diff(sm)
                        mn = (dd[:-1] < 0) & (dd[1:] >= 0)
                        subs.append(float((sm[1:-1][mn] < 0.7 * seg.max()).sum()))
                    s_list.append(s)
                    e_list.append(en)
                if straight:
                    f["m_straight"] = float(np.mean(straight))
                    f["m_stroke_len"] = float(np.mean(lens))
                    f["m_stroke_dur"] = float(np.mean(durs))
                    f["m_stroke_rate"] = len(straight) / dur
                    f["m_peak_speed"] = float(np.mean(peaks))
                    f["m_peak_pos"] = float(np.mean(peakpos))
                    if subs:
                        f["m_submoves"] = float(np.mean(subs))
                    if len(lens) >= 3:
                        f["m_stroke_len_cv"] = float(np.std(lens) / max(np.mean(lens), 1e-6))
                    if len(s_list) >= 4:
                        gaps = np.array([(s_list[i + 1] - e_list[i]) * GRID_DT
                                         for i in range(len(s_list) - 1)])
                        gaps = gaps[(gaps >= 0) & (gaps < 2.0)]
                        if len(gaps) >= 3:
                            f["m_pause_mean"] = float(gaps.mean())
                            f["m_pause_cv"] = float(gaps.std() / max(gaps.mean(), 1e-6))
                    stroke_ends = [float(grid[min(en, len(grid) - 1)]) for en in e_list]

    # ---- clicks ------------------------------------------------------------
    downs, holds, pend, left = [], [], {}, 0
    for t, b, d in buttons:
        if d:
            downs.append(t)
            pend[b] = t
            left += (b == "left")
        elif b in pend:
            holds.append(t - pend.pop(b))
    f["c_rate"] = len(downs) / dur
    if downs:
        f["c_left_ratio"] = left / len(downs)
    holds = [h for h in holds if h < 1.5]
    if len(holds) >= 3:
        f["c_hold_mean"], f["c_hold_std"] = float(np.mean(holds)), float(np.std(holds))
        f["c_hold_med"] = float(np.median(holds))
    if len(downs) >= 4:
        iv = np.diff(downs)
        iv = iv[iv < 3]
        if len(iv) >= 3:
            f["c_int_mean"], f["c_int_std"] = float(iv.mean()), float(iv.std())
            f["c_fast_ratio"] = float((iv < 0.25).mean())
            f["c_int_med"] = float(np.median(iv))
            f["c_int_cv"] = float(iv.std() / max(iv.mean(), 1e-6))
    if stroke_ends and downs:                                # how soon after a stroke ends do you click?
        se = np.asarray(stroke_ends)
        lat = []
        for d_ in downs:
            prev = se[se <= d_]
            if len(prev) and d_ - prev[-1] < 1.0:
                lat.append(d_ - prev[-1])
        if len(lat) >= 3:
            f["m_click_lat"] = float(np.mean(lat))

    # ---- ADS / right-click ---------------------------------------------------
    t_end = _window_end([e[0] for e in buttons] + ([moves[-1][0]] if moves else []), dur)
    t_start = t_end - dur
    r_downs, r_holds, ivs, rp = [], [], [], None
    for t, b, d in sorted(buttons, key=lambda e: e[0]):
        if b != "right":
            continue
        if d:
            if rp is None:
                r_downs.append(t)
                rp = t
        elif rp is not None:
            r_holds.append(t - rp)
            ivs.append((rp, t, True))
            rp = None
        else:                                   # released a hold that began before this window
            ivs.append((t_start, t, False))
    if rp is not None:                          # still held at the end of the window
        ivs.append((rp, t_end, True))
    f["r_rate"] = len(r_downs) / dur
    f["r_time_ratio"] = float(min(1.0, sum(max(0.0, b - a) for a, b, _ in ivs) / dur))
    rh = np.asarray([h for h in r_holds if 0 < h < 10.0])
    if len(rh) >= 3:
        f["r_hold_mean"], f["r_hold_std"] = float(rh.mean()), float(rh.std())
        f["r_hold_med"], f["r_hold_p90"] = float(np.median(rh)), float(np.percentile(rh, 90))
        f["r_tap_ratio"] = float((rh < 0.25).mean())
        f["r_hold_ratio"] = float((rh >= 0.6).mean())
    if len(r_downs) >= 4:
        riv = np.diff(r_downs)
        riv = riv[riv < 5]
        if len(riv) >= 3:
            f["r_int_med"] = float(np.median(riv))
            f["r_int_cv"] = float(riv.std() / max(riv.mean(), 1e-6))
    lefts = [t for t, b, d in buttons if d and b == "left"]
    if ivs and len(lefts) >= 3:                 # how much of your shooting happens while aiming
        f["r_fire_ratio"] = float(sum(any(a <= t <= b for a, b, _ in ivs) for t in lefts) / len(lefts))
    lat = []
    for a, b, known in ivs:
        if known:
            first = [t for t in lefts if a <= t <= b]
            if first and first[0] - a < 5.0:
                lat.append(first[0] - a)
    if len(lat) >= 3:
        f["r_fire_lat"] = float(np.mean(lat))

    # ---- scroll ------------------------------------------------------------
    f["s_rate"] = len(scrolls) / dur
    if scrolls:
        f["s_mag"] = float(np.mean([s[1] for s in scrolls]))

    # ---- keyboard ----------------------------------------------------------
    pend, holds, down_t, names, overlap = {}, [], [], [], 0
    bucket_holds = {i: [] for i in range(N_BUCKETS)}
    for t, d, n in keys:
        if d:
            if pend:
                overlap += 1
            pend[n] = t
            down_t.append(t)
            names.append(n)
        elif n in pend:
            h = t - pend.pop(n)
            if h < 2.0:
                holds.append(h)
                bucket_holds[zlib.crc32(n.encode()) % N_BUCKETS].append(h)
    f["k_rate"] = len(down_t) / dur
    if len(down_t) >= 5:
        f["k_overlap"] = overlap / len(down_t)
        f["k_unique"] = float(len(set(names)))
        cnt = Counter(names)
        p = np.array(list(cnt.values()), float) / len(names)
        f["k_entropy"] = float(-(p * np.log2(p)).sum())
        for i in range(N_BUCKETS):
            f[f"k_freq_b{i}"] = 0.0
        for n, c in cnt.items():
            f[f"k_freq_b{zlib.crc32(n.encode()) % N_BUCKETS}"] += c / len(names)
        fl_all = np.diff(down_t)
        keep = fl_all < 2.0
        fl = fl_all[keep]
        if len(fl) >= 4:
            f["k_flight_mean"], f["k_flight_std"] = float(fl.mean()), float(fl.std())
            f["k_flight_med"] = float(np.median(fl))
            f["k_flight_cv"] = float(fl.std() / max(fl.mean(), 1e-6))
            f["k_flight_p10"], f["k_flight_p90"] = float(np.percentile(fl, 10)), float(np.percentile(fl, 90))
            f["k_burst_ratio"] = float((fl < 0.15).mean())
            f["k_pause_ratio"] = float((fl > 0.8).mean())
            if len(fl) >= 8 and fl[:-1].std() > 0 and fl[1:].std() > 0:
                ac = float(np.corrcoef(fl[:-1], fl[1:])[0, 1])
                if ac == ac:
                    f["k_flight_ac1"] = ac
            fb = {i: [] for i in range(N_BUCKETS)}           # flight time INTO each key bucket
            for j in np.where(keep)[0]:
                fb[zlib.crc32(names[j + 1].encode()) % N_BUCKETS].append(fl_all[j])
            for i, vs in fb.items():
                if len(vs) >= 2:
                    f[f"k_flight_b{i}"] = float(np.mean(vs))
    if len(holds) >= 4:
        h = np.asarray(holds)
        f["k_hold_mean"], f["k_hold_std"] = float(h.mean()), float(h.std())
        f["k_hold_med"], f["k_hold_p90"] = float(np.median(h)), float(np.percentile(h, 90))
        f["k_hold_cv"] = float(h.std() / max(h.mean(), 1e-6))
        f["k_hold_p10"] = float(np.percentile(h, 10))
    for i, hs in bucket_holds.items():
        if len(hs) >= 2:
            f[f"k_hold_b{i}"] = float(np.mean(hs))
    return f


def window_vector(snap, dur):
    moves, buttons, scrolls, keys = snap
    n_down = sum(1 for e in keys if e[1])
    if len(moves) < MIN_MOVES and n_down < MIN_KEYS:
        return None
    f = extract_features(moves, buttons, scrolls, keys, dur)
    vec = np.array([f[n] for n in FEATURE_NAMES], float)
    return vec if np.sum(~np.isnan(vec)) >= 10 else None


def distance(vec, mu, sd, w=None):
    """Mean clipped z-distance over shared features; w = optional per-feature weights."""
    ok = ~np.isnan(vec) & ~np.isnan(mu)
    if ok.sum() < 10:
        return None
    z = np.minimum(np.abs(vec[ok] - mu[ok]) / sd[ok], 4.0)
    if w is None:
        return float(np.mean(z))
    ww = w[ok]
    return float((z * ww).sum() / ww.sum())


IDX = {n: i for i, n in enumerate(FEATURE_NAMES)}
