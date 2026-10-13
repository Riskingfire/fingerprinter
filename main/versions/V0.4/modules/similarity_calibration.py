"""Population-relative similarity calibration (headless core).

Fixes the "everyone is within 5% of everyone else because of the game's keybinds"
problem. It NEVER modifies stored profiles - it only reads them and adds a
calibrated scoring layer. Nothing runs in the background: calibration happens only
when something calls ``service.recalibrate()`` (the UI button, or the
``calibrate`` command-line command).

What a recalibration does
  1. drops features that barely vary across the population (keybind-locked)
  2. z-scores the rest against the population
  3. weights features by between-player vs within-player variance (needs sessions)
  4. scores each pair by its percentile among ALL pairs (+ a z-score), plus a
     log-stretched 0-100 display score (display only, never used for flagging)
  5. refuses to flag anyone when scores are not informative

This file has no tkinter code. The window lives in similarity_calibration_ui.py,
which is only loaded when the ``ui_tk`` module is present.

Services provided : "calibration"  (CalibrationService)
Commands added    : python fingerprinter.py calibrate
Override hook     : ctx.provide("calibration_profile_source", fn) where
                    fn() -> (profiles, sessions_or_None)   (see collect_profiles)
"""

NAME = "similarity_calibration"
DESCRIPTION = "Population-relative similarity scoring (fixes keybind-driven 'everyone matches')"
REQUIRES = []
OPTIONAL = []
ORDER = 60

import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

try:  # the loader catches this and shows the reason instead of crashing
    from fp.plugins import ModuleUnavailable
except Exception:  # pragma: no cover - running outside the app
    class ModuleUnavailable(Exception):
        pass

try:
    import numpy as np
except ImportError as _e:  # numpy missing from this install / build
    raise ModuleUnavailable("numpy is not installed") from _e

try:
    from fp.settings import APP_DATA_DIR
except Exception:  # pragma: no cover
    APP_DATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "fingerprint")

CALIBRATION_FILE = os.path.join(APP_DATA_DIR, "calibration.json")
DEFAULT_PROFILES_DIR = os.path.join(APP_DATA_DIR, "profiles")

Profile = Dict[str, float]
Profiles = Dict[str, Profile]
Sessions = Dict[str, List[Profile]]


# =====================================================================================
# Config / results
# =====================================================================================

@dataclass
class CalibrationConfig:
    min_cv: float = 0.02                 # drop features with std/|mean| below this
    min_abs_std: float = 1e-9
    excluded_features: Tuple[str, ...] = ()   # known keybind/layout features
    min_profiles: int = 10
    max_profiles: int = 4000             # n^2 memory guard
    min_distance_spread: float = 0.08    # std/mean of pairwise distances
    min_median_separation: float = 1.0   # median between/within var ratio (needs sessions)
    flag_percentile: float = 99.5
    flag_zscore: float = 2.5
    use_separation_weights: bool = True
    min_sessions_for_within: int = 2
    max_pairs_kept: int = 500            # closest pairs kept in the report


@dataclass
class PairResult:
    a: str
    b: str
    distance: float
    percentile: float      # 0-100, share of ALL pairs that are farther apart
    z: float               # std-devs closer than the typical pair
    display_score: float   # 0-100 log-stretched, display only
    flagged: bool


@dataclass
class CalibrationReport:
    n_profiles: int
    n_features_in: int
    n_features_used: int
    dropped_features: List[str]
    feature_weights: Dict[str, float]
    distance_mean: float
    distance_std: float
    distance_spread: float
    informative: bool
    median_separation: Optional[float] = None
    warnings: List[str] = field(default_factory=list)
    pairs: List[PairResult] = field(default_factory=list)   # closest first

    def flagged_pairs(self) -> List[PairResult]:
        return [p for p in self.pairs if p.flagged]

    def summary(self) -> str:
        lines = [
            f"Profiles: {self.n_profiles}",
            f"Features: {self.n_features_used}/{self.n_features_in} used "
            f"({len(self.dropped_features)} dropped as non-discriminating)",
            f"Pairwise distance mean={self.distance_mean:.3f} "
            f"std={self.distance_std:.3f} spread={self.distance_spread:.3f}",
        ]
        if self.median_separation is not None:
            lines.append(f"Median feature separation (between/within): "
                         f"{self.median_separation:.2f}")
        lines.append(f"Informative: {'yes' if self.informative else 'NO'}")
        for w in self.warnings:
            lines.append(f"WARNING: {w}")
        if self.informative:
            flagged = self.flagged_pairs()
            lines.append(f"Pairs flagged for review: {len(flagged)}")
            for p in flagged[:20]:
                lines.append(f"  {p.a} <-> {p.b}  pct={p.percentile:.2f} "
                             f"z={p.z:.2f} score={p.display_score:.1f}")
        else:
            lines.append("No pairs flagged (scores are not informative).")
        lines.append("Flags are evidence for human review, not proof of identity.")
        return "\n".join(lines)


# =====================================================================================
# Calibration engine
# =====================================================================================

_QUANTILES = np.linspace(0.0, 1.0, 1001)


def _to_matrix(profiles: Dict[str, Profile], names: Sequence[str]) -> "np.ndarray":
    m = np.full((len(profiles), len(names)), np.nan, dtype=float)
    for i, prof in enumerate(profiles.values()):
        for j, f in enumerate(names):
            v = prof.get(f)
            if v is not None:
                m[i, j] = float(v)
    return m


class Calibrator:
    def __init__(self, config: Optional[CalibrationConfig] = None):
        self.config = config or CalibrationConfig()
        self.report: Optional[CalibrationReport] = None
        self.features: List[str] = []
        self.mean = self.std = self.scale = None
        self.dist_quantiles = None     # 1001 points describing the pair-distance CDF

    # ---- the button entry point --------------------------------------------------
    def recalibrate(self, profiles: Profiles,
                    sessions: Optional[Sessions] = None) -> CalibrationReport:
        cfg = self.config
        names = list(profiles)
        n = len(names)
        warnings: List[str] = []
        if n > cfg.max_profiles:
            raise ValueError(f"{n} profiles exceeds max_profiles={cfg.max_profiles}; "
                             "raise CalibrationConfig.max_profiles if you have the RAM.")

        all_features = sorted({f for p in profiles.values() for f in p})
        cands = [f for f in all_features if f not in cfg.excluded_features]
        X = _to_matrix(profiles, cands) if cands else np.zeros((n, 0))

        # 1. drop near-constant (keybind-locked) features
        with np.errstate(invalid="ignore"):
            mu = np.nanmean(X, axis=0) if X.size else np.zeros(0)
            sd = np.nanstd(X, axis=0, ddof=1) if n > 1 and X.size else np.zeros(X.shape[1])
        keep = np.zeros(len(cands), dtype=bool)
        for j in range(len(cands)):
            if np.isfinite(sd[j]) and sd[j] >= cfg.min_abs_std and \
                    sd[j] / max(abs(mu[j]), 1e-12) >= cfg.min_cv:
                keep[j] = True
        used = [f for f, k in zip(cands, keep) if k]
        dropped = [f for f in all_features if f not in used]

        if n < cfg.min_profiles:
            warnings.append(f"Only {n} profiles (need >= {cfg.min_profiles}); "
                            "population statistics are unreliable.")

        def empty(msg: str) -> CalibrationReport:
            warnings.append(msg)
            self.report = CalibrationReport(n, len(all_features), len(used), dropped,
                                            {}, 0.0, 0.0, 0.0, False, None, warnings)
            return self.report

        if not used:
            return empty("No discriminating features remain after filtering.")
        if n < 2:
            return empty("Need at least 2 profiles to compare.")

        mu_u, sd_u = mu[keep], sd[keep]
        # 2. z-score against the population (missing -> population average)
        Z = np.nan_to_num((X[:, keep] - mu_u) / sd_u, nan=0.0)

        # 3. separation weights
        w = np.ones(len(used))
        median_sep: Optional[float] = None
        if cfg.use_separation_weights and sessions:
            sep = self._separation_weights(profiles, sessions, used, mu_u, sd_u)
            if sep is not None:
                w, F = sep
                median_sep = float(np.median(F))
            else:
                warnings.append("Not enough multi-session players for separation "
                                "weighting; using equal weights.")
        elif cfg.use_separation_weights:
            warnings.append("No per-session data supplied; using equal feature weights "
                            "and cannot verify that features separate players. "
                            "Supplying sessions makes this far more robust to keybinds.")
        w = w / w.sum() * len(w)

        # 4. pairwise weighted distances (n^2 memory, not n^2 * F)
        scale = np.sqrt(w / len(used))
        Zw = Z * scale
        sq = np.einsum("ij,ij->i", Zw, Zw)
        D2 = sq[:, None] + sq[None, :] - 2.0 * (Zw @ Zw.T)
        iu = np.triu_indices(n, k=1)
        dists = np.sqrt(np.clip(D2[iu], 0.0, None))
        del D2

        d_mean = float(dists.mean())
        d_std = float(dists.std(ddof=1)) if len(dists) > 1 else 0.0
        spread = d_std / d_mean if d_mean > 0 else 0.0

        # 5. is this population informative?
        informative = n >= cfg.min_profiles
        if spread < cfg.min_distance_spread:
            informative = False
            warnings.append(f"Pairwise distance spread is {spread:.3f} "
                            f"(< {cfg.min_distance_spread}); everyone is about equally "
                            "similar. Scores are not informative with this feature set.")
        if median_sep is not None and median_sep < cfg.min_median_separation:
            informative = False
            warnings.append(f"Median between/within-player variance ratio is "
                            f"{median_sep:.2f} (< {cfg.min_median_separation}); the "
                            "remaining features vary as much within one player as "
                            "between players, so scores are not informative.")

        # 6. percentiles / z / display score for the closest pairs
        sorted_d = np.sort(dists)
        self.dist_quantiles = np.quantile(sorted_d, _QUANTILES)
        k = min(cfg.max_pairs_kept, len(dists))
        top = np.argpartition(dists, k - 1)[:k] if k < len(dists) else np.arange(len(dists))
        top = top[np.argsort(dists[top])]
        d_min, d_max = float(sorted_d[0]), float(sorted_d[-1])
        pairs: List[PairResult] = []
        for idx in top:
            d = float(dists[idx])
            pct = 100.0 * (1.0 - np.searchsorted(sorted_d, d, side="left") / len(sorted_d))
            z = (d_mean - d) / d_std if d_std > 0 else 0.0
            flagged = bool(informative and pct >= cfg.flag_percentile and z >= cfg.flag_zscore)
            pairs.append(PairResult(names[iu[0][idx]], names[iu[1][idx]], d, float(pct),
                                    float(z), self._log_stretch(d, d_min, d_max), flagged))

        self.features, self.mean, self.std, self.scale = used, mu_u, sd_u, scale
        self.report = CalibrationReport(n, len(all_features), len(used), dropped,
                                        dict(zip(used, map(float, w))), d_mean, d_std,
                                        spread, informative, median_sep, warnings, pairs)
        return self.report

    # ---- score a pair with the last fitted calibration ---------------------------
    def score_pair(self, a: Profile, b: Profile) -> Optional[Tuple[float, float]]:
        """-> (percentile, z) or None if not fitted / not informative."""
        if self.mean is None or not self.report or not self.report.informative:
            return None
        va = np.array([a.get(f, np.nan) for f in self.features], dtype=float)
        vb = np.array([b.get(f, np.nan) for f in self.features], dtype=float)
        za = np.nan_to_num((va - self.mean) / self.std, nan=0.0)
        zb = np.nan_to_num((vb - self.mean) / self.std, nan=0.0)
        d = float(np.sqrt(np.sum(((za - zb) * self.scale) ** 2)))
        farther = 1.0 - float(np.interp(d, self.dist_quantiles, _QUANTILES))
        return 100.0 * farther, (self.report.distance_mean - d) / self.report.distance_std

    # ---- internals ---------------------------------------------------------------
    def _separation_weights(self, profiles, sessions, used, mu, sd):
        """w_f = F/(F+1), F = between-player var / within-player var."""
        cfg = self.config
        within, means = [], []
        for pid, sess in sessions.items():
            if pid not in profiles or len(sess) < cfg.min_sessions_for_within:
                continue
            S = (_to_matrix({str(i): s for i, s in enumerate(sess)}, used) - mu) / sd
            with np.errstate(invalid="ignore"):
                within.append(np.nanvar(S, axis=0, ddof=1))
                means.append(np.nanmean(S, axis=0))
        if len(within) < 3:
            return None
        wv = np.nan_to_num(np.nanmean(np.vstack(within), axis=0), nan=1.0)
        bv = np.nan_to_num(np.nanvar(np.vstack(means), axis=0, ddof=1), nan=0.0)
        F = bv / (wv + 1e-9)
        return np.clip(F / (F + 1.0), 0.02, None), F

    @staticmethod
    def _log_stretch(d: float, d_min: float, d_max: float) -> float:
        """Display only: log-stretch so differences among the closest pairs are visible."""
        span = d_max - d_min
        if span <= 0:
            return 50.0
        t = (d - d_min) / span
        return 100.0 * (1.0 - math.log1p(50.0 * t) / math.log1p(50.0))

    # ---- persistence (calibration only; never profiles) --------------------------
    def save(self, path: str = CALIBRATION_FILE) -> None:
        if self.mean is None:
            raise RuntimeError("Nothing to save; run recalibrate() first.")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"features": self.features, "mean": self.mean.tolist(),
                       "std": self.std.tolist(), "scale": self.scale.tolist(),
                       "dist_quantiles": self.dist_quantiles.tolist(),
                       "distance_mean": self.report.distance_mean,
                       "distance_std": self.report.distance_std,
                       "informative": self.report.informative,
                       "config": asdict(self.config)}, fh)

    @classmethod
    def load(cls, path: str = CALIBRATION_FILE) -> "Calibrator":
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        cfgd = d["config"]
        cfgd["excluded_features"] = tuple(cfgd.get("excluded_features", ()))
        cal = cls(CalibrationConfig(**cfgd))
        cal.features = d["features"]
        cal.mean, cal.std = np.array(d["mean"]), np.array(d["std"])
        cal.scale, cal.dist_quantiles = np.array(d["scale"]), np.array(d["dist_quantiles"])
        cal.report = CalibrationReport(0, 0, len(cal.features), [], {}, d["distance_mean"],
                                       d["distance_std"],
                                       d["distance_std"] / max(d["distance_mean"], 1e-12),
                                       d["informative"])
        return cal


# =====================================================================================
# Data adapter  <-- the ONE place to adapt to how V0.4 stores profiles
# =====================================================================================

def _flatten(obj, prefix="") -> Dict[str, float]:
    """Flatten nested dicts/lists to {dotted.name: float}, keeping numeric leaves only."""
    out: Dict[str, float] = {}
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        if math.isfinite(obj):
            out[prefix or "value"] = float(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list) and len(obj) <= 256:
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    return out


def scan_json_profiles(profiles_dir: str) -> Tuple[Profiles, Optional[Sessions]]:
    """DEFAULT ADAPTER (a best guess - replace it once you point me at the real format).

    Treats every ``*.json`` file in `profiles_dir` as one profile (name = file stem) and
    flattens its numeric values into features. If a file has a top-level "sessions" list
    of dicts, those become that player's per-session feature dicts.
    """
    profiles: Profiles = {}
    sessions: Sessions = {}
    if not os.path.isdir(profiles_dir):
        return profiles, None
    for fn in sorted(os.listdir(profiles_dir)):
        if not fn.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(profiles_dir, fn), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        pid = fn[:-5]
        sess = data.get("sessions")
        body = {k: v for k, v in data.items() if k != "sessions"}
        flat = _flatten(body)
        if flat:
            profiles[pid] = flat
        if isinstance(sess, list):
            sf = [_flatten(s) for s in sess if isinstance(s, dict)]
            sf = [s for s in sf if s]
            if sf:
                sessions[pid] = sf
    return profiles, (sessions or None)


def collect_profiles(ctx, profiles_dir: Optional[str] = None):
    """-> (profiles, sessions_or_None). Another module can take over by calling
    ctx.provide("calibration_profile_source", fn) with fn() -> (profiles, sessions)."""
    src = ctx.get("calibration_profile_source") if ctx is not None else None
    if callable(src):
        return src()
    return scan_json_profiles(profiles_dir or DEFAULT_PROFILES_DIR)


# =====================================================================================
# Service + command-line command + register()
# =====================================================================================

class CalibrationService:
    """What other modules (e.g. the UI) talk to. Never runs on its own."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.config = CalibrationConfig()
        self.calibrator = Calibrator(self.config)
        self.last_report: Optional[CalibrationReport] = None
        self.profiles_dir: Optional[str] = None
        if os.path.isfile(CALIBRATION_FILE):
            try:  # restore the last fitted calibration so score_pair works after restart
                self.calibrator = Calibrator.load(CALIBRATION_FILE)
                self.calibrator.config = self.config
            except Exception:
                pass

    def recalibrate(self) -> CalibrationReport:
        """Run one full calibration pass. Call from a button / command only."""
        profiles, sessions = collect_profiles(self.ctx, self.profiles_dir)
        if not profiles:
            raise RuntimeError("No profiles found. Looked in: "
                               f"{self.profiles_dir or DEFAULT_PROFILES_DIR} (or the "
                               "'calibration_profile_source' service).")
        cal = Calibrator(self.config)
        report = cal.recalibrate(profiles, sessions)
        if report.n_features_used and report.n_profiles >= 2:
            try:
                cal.save(CALIBRATION_FILE)
            except OSError:
                report.warnings.append("Could not save calibration file.")
        self.calibrator, self.last_report = cal, report
        return report

    def score_pair(self, a: Profile, b: Profile):
        return self.calibrator.score_pair(a, b)


def _setup_cli(parser):
    parser.add_argument("--profiles-dir", default=None, help="folder of profile .json files")
    parser.add_argument("--flag-z", type=float, default=None)
    parser.add_argument("--flag-percentile", type=float, default=None)


def _run_cli(ctx, args) -> int:
    svc = ctx.get("calibration")
    if getattr(args, "profiles_dir", None):
        svc.profiles_dir = args.profiles_dir
    if getattr(args, "flag_z", None) is not None:
        svc.config.flag_zscore = args.flag_z
    if getattr(args, "flag_percentile", None) is not None:
        svc.config.flag_percentile = args.flag_percentile
    try:
        print(svc.recalibrate().summary())
        return 0
    except Exception as e:
        print(f"calibrate failed: {e}", file=sys.stderr)
        return 1


def register(ctx):
    svc = CalibrationService(ctx)
    ctx.provide("calibration", svc)
    ctx.add_command("calibrate", _run_cli,
                    help="recalibrate similarity scores against the whole population",
                    setup=_setup_cli)
