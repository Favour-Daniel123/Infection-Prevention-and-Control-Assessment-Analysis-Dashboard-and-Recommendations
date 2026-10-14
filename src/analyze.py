"""
IPCAF facility assessment - analysis pipeline.

Reads the completed WHO IPCAF workbook, validates the scoring, derives gap /
theme / roadmap metrics, and writes:
  data/processed/ipcaf_items.csv       one row per indicator (122 rows)
  data/processed/ipcaf_summary.csv     one row per core component
  data/processed/ipcaf_themes.csv      cross-cutting theme scores
  data/processed/ipcaf_roadmap.csv     improvement actions with point potential
  data/processed/dashboard_data.json   everything the dashboard needs
  reports/figures/*.png                static charts used in the README

Run:  python src/analyze.py
"""
import json
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from openpyxl import load_workbook

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/Response--IPCAF_WHO_Assessment_Tool.xlsx"
PROC = ROOT / "data/processed"
FIG = ROOT / "reports/figures"

LEVELS = [(0, 200, "Inadequate"), (201, 400, "Basic"),
          (401, 600, "Intermediate"), (601, 800, "Advanced")]

CC_NAMES = {
    1: "IPC programme",
    2: "IPC guidelines",
    3: "IPC education and training",
    4: "HAI surveillance",
    5: "Multimodal strategies",
    6: "Monitoring/audit and feedback",
    7: "Workload, staffing and bed occupancy",
    8: "Built environment, materials and equipment",
}
CC_SHORT = {1: "Programme", 2: "Guidelines", 3: "Training", 4: "Surveillance",
            5: "Multimodal", 6: "Monitoring", 7: "Staffing/beds", 8: "Environment"}

# Indicators whose scoring is reversed (answering "No" is the desirable answer).
REVERSE_SCORED = {("CC7", "6")}


def level_of(score):
    for lo, hi, name in LEVELS:
        if lo <= score <= hi:
            return name
    return "Advanced"


# ---------------------------------------------------------------- 1. LOAD
def load_items():
    wb = load_workbook(RAW, data_only=True)
    rows, subtotals = [], {}
    for n in range(1, 9):
        ws = wb[f"CC{n}"]
        for r in range(4, ws.max_row + 1):
            no, q, ans, sc, mx = (ws.cell(r, c).value for c in range(1, 6))
            if no is None:
                continue
            if str(no).startswith("Subtotal"):
                subtotals[n] = (sc, mx)
                continue
            rows.append(dict(cc=n, cc_name=CC_NAMES[n], no=str(no), question=q,
                             answer=ans, score=float(sc or 0), max=float(mx)))
    s = wb["Summary"]
    summary = {"total": s["B15"].value, "level": s["B16"].value,
               "cc": {n: s.cell(4 + n, 2).value for n in range(1, 9)}}
    return pd.DataFrame(rows), subtotals, summary


# ---------------------------------------------------------------- 2. VALIDATE
def validate(df, subtotals, summary):
    checks = []
    for n in range(1, 9):
        calc = df.loc[df.cc == n, "score"].sum()
        checks.append((f"CC{n} item sum == sheet subtotal", calc == subtotals[n][0]))
        checks.append((f"CC{n} max == 100", df.loc[df.cc == n, "max"].sum() == 100))
        checks.append((f"CC{n} == Summary sheet", calc == summary["cc"][n]))
    checks.append(("No score exceeds its max", bool((df.score <= df["max"]).all())))
    checks.append(("Total == Summary total", df.score.sum() == summary["total"]))
    checks.append(("Level matches thresholds",
                   level_of(df.score.sum()) == summary["level"].split(". ")[1]))
    for name, ok in checks:
        print(("PASS  " if ok else "FAIL  ") + name)
    assert all(ok for _, ok in checks), "Validation failed"
    return checks


# ---------------------------------------------------------------- 3. ENRICH
PREFIX = {"Guidelines available for": "Guideline", "Surveillance conducted for": "Surveillance",
          "Monitor": "Monitor", "Feedback up-to-date surveillance information to": "Surveillance feedback",
          "Feedback auditing reports": "Audit feedback", "IPC committee includes": "Committee",
          "Senior leadership commitment": "Leadership"}


def label(q):
    q = q.rstrip("?")
    if q.startswith("Multimodal element") and " - " in q:
        return "Multimodal: " + q.split(" - ", 1)[1]
    if ": " in q:
        p, rest = q.split(": ", 1)
        rest = rest.replace("Yes, ", "")
        return f"{PREFIX.get(p, p)}: {rest}"
    return q if len(q) <= 78 else q[:75] + "..."


THEMES = {
    "Frontline engagement": [("CC2", "5"), ("CC2", "7"), ("CC3", "2"), ("CC3", "3"), ("CC3", "4"),
                             ("CC3", "9"), ("CC4", "14a"), ("CC6", "5c")],
    "Data-to-action feedback loop": [("CC4", "12"), ("CC4", "14a"), ("CC4", "14b"), ("CC4", "14c"),
                                     ("CC4", "14d"), ("CC4", "15"), ("CC6", "5a"), ("CC6", "5b"),
                                     ("CC6", "5c"), ("CC6", "5d"), ("CC6", "5e"), ("CC6", "6"),
                                     ("CC6", "7"), ("CC2", "8"), ("CC5", "2c")],
    "Device & procedure-related HAI prevention": [("CC2", "2e"), ("CC2", "2f"), ("CC2", "2g"), ("CC2", "2h"),
                                                  ("CC2", "2i"), ("CC2", "2j"), ("CC4", "6a"), ("CC4", "6b"),
                                                  ("CC4", "6d"), ("CC6", "3b"), ("CC6", "3c"), ("CC6", "3d")],
    "Water, sanitation & utilities": [("CC8", "1"), ("CC8", "2"), ("CC8", "3"), ("CC8", "4"),
                                      ("CC8", "5"), ("CC8", "14")],
    "Staffing & capacity planning": [("CC7", "1"), ("CC7", "2"), ("CC7", "3"), ("CC7", "4"),
                                     ("CC7", "7"), ("CC7", "8")],
    "Leadership, planning & resourcing": [("CC1", "3"), ("CC1", "7c"), ("CC1", "8"), ("CC1", "9a"),
                                          ("CC6", "2"), ("CC4", "1"), ("CC4", "2"), ("CC4", "5"), ("CC4", "7")],
}


def enrich(df):
    df = df.copy()
    df["id"] = "CC" + df.cc.astype(str) + "." + df.no
    df["label"] = df.question.map(label)
    df["gap"] = df["max"] - df["score"]
    df["pct"] = df.score / df["max"]
    df["status"] = np.where(df.score == df["max"], "Fully met",
                            np.where(df.score == 0, "Not met", "Partially met"))
    df["unanswered"] = df.answer.isna()
    df["reverse_scored"] = [(f"CC{c}", n) in REVERSE_SCORED for c, n in zip(df.cc, df.no)]
    df["themes"] = [[t for t, keys in THEMES.items() if (f"CC{c}", n) in keys]
                    for c, n in zip(df.cc, df.no)]
    return df


# ---------------------------------------------------------------- 4. ROADMAP
# Each action = indicators that become "fully met". Phase/effort is the analyst's
# judgement (cost, lead-time, dependency), NOT a WHO classification.
ROADMAP = [
    # Phase 1 - governance, planning and quick operational fixes (0-3 months)
    (1, "Define measurable IPC objectives for priority areas", [("CC1", "8")], "Low"),
    (1, "Write a monitoring plan with goals, targets and activities", [("CC6", "2")], "Low"),
    (1, "Report monitoring data at least annually", [("CC6", "6")], "Low"),
    (1, "Designate surveillance lead and add surveillance to the IPC programme", [("CC4", "1"), ("CC4", "2")], "Low"),
    (1, "Run a HAI prioritisation exercise for local context", [("CC4", "5")], "Low"),
    (1, "Involve frontline staff and stakeholders in guideline planning", [("CC2", "5"), ("CC2", "6")], "Low"),
    (1, "Feed surveillance results back to frontline, clinical leads and management",
     [("CC4", "14a"), ("CC4", "14b"), ("CC4", "14d")], "Low"),
    (1, "Share audit results with IPC team and frontline staff", [("CC6", "5a"), ("CC6", "5c")], "Low"),
    (1, "Introduce IPC bundles and checklists", [("CC5", "5")], "Low"),
    (1, "Add facility management to the IPC committee", [("CC1", "7c")], "Low"),
    (1, "Install hand-hygiene stations at all points of care", [("CC8", "3")], "Medium"),
    (1, "Audit waste management practice", [("CC6", "3i")], "Low"),
    # Phase 2 - capability building (3-6 months)
    (2, "Develop guidelines for SSI, CLABSI, HAP, CAUTI, MDR, disinfection, HCW safety, antibiotic stewardship",
     [("CC2", k) for k in ("2e", "2f", "2g", "2h", "2i", "2j", "2k", "2n")], "Medium"),
    (2, "Train staff on new/updated guidelines", [("CC2", "7")], "Medium"),
    (2, "Run regular IPC training for health care workers", [("CC3", "3")], "Medium"),
    (2, "Run regular IPC training for cleaners and support staff", [("CC3", "4")], "Medium"),
    (2, "Create link nurse / IPC champion network", [("CC3", "2")], "Medium"),
    (2, "Train administrators and managers in IPC", [("CC3", "5")], "Low"),
    (2, "Train surveillance staff; standardise data collection and quality review",
     [("CC4", "3"), ("CC4", "9"), ("CC4", "10")], "Medium"),
    (2, "Start surveillance for SSI, device-associated and MDR infections",
     [("CC4", "6a"), ("CC4", "6b"), ("CC4", "6d")], "Medium"),
    (2, "Audit catheter care, wound dressing, isolation and ward cleaning",
     [("CC6", "3b"), ("CC6", "3c"), ("CC6", "3d"), ("CC6", "3e")], "Medium"),
    (2, "Use surveillance data to plan unit-level improvements and review AMR",
     [("CC4", "12"), ("CC4", "13")], "Medium"),
    (2, "Introduce daily signed cleaning records", [("CC8", "7")], "Low"),
    (2, "Allocate a dedicated IPC budget", [("CC1", "9a")], "Medium"),
    # Phase 3 - structural / capital investment (6-12+ months)
    (3, "Appoint a full-time IPC professional", [("CC1", "3")], "High"),
    (3, "Assess staffing (WISN), maintain agreed ratios, act on shortfalls",
     [("CC7", "1"), ("CC7", "2"), ("CC7", "3")], "High"),
    (3, "Meet ward design and >1 m bed-spacing standards; respond to over-capacity",
     [("CC7", "4"), ("CC7", "7"), ("CC7", "8")], "High"),
    (3, "Secure reliable water, drinking-water stations and wastewater treatment",
     [("CC8", "1"), ("CC8", "2"), ("CC8", "14")], "High"),
    (3, "Secure continuous power, PPE, supplies and sterile-equipment availability",
     [("CC8", "5"), ("CC8", "10"), ("CC8", "16"), ("CC8", "17")], "High"),
    (3, "Make laboratory support and IT for surveillance reliable", [("CC1", "10"), ("CC4", "4")], "High"),
]


def build_roadmap(df):
    out, used = [], {}
    idx = df.set_index("id")
    for i, (phase, name, keys, effort) in enumerate(ROADMAP):
        ids = [f"{c}.{n}" for c, n in keys]
        for k in ids:
            used.setdefault(k, []).append(name)
        pts = float(idx.loc[ids, "gap"].sum())
        out.append(dict(id=f"A{i+1}", phase=phase, action=name, indicators=ids,
                        points=pts, effort=effort))
    dupes = {k: v for k, v in used.items() if len(v) > 1}
    assert not dupes, f"Indicator double-counted in roadmap: {dupes}"
    return out


# ---------------------------------------------------------------- 5. FIGURES
C = dict(ink="#1f2a37", grey="#8b95a1", teal="#0f766e", green="#2a9d6f",
         amber="#e0a030", red="#d1495b", light="#e8ecf0")


def style():
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.spines.top": False,
                         "axes.spines.right": False, "axes.edgecolor": C["grey"],
                         "axes.labelcolor": C["ink"], "text.color": C["ink"],
                         "xtick.color": C["ink"], "ytick.color": C["ink"], "figure.dpi": 150})


def fig_cc(summ):
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    s = summ.sort_values("pct")
    cols = [C["red"] if p < .35 else C["amber"] if p < .5 else C["teal"] for p in s.pct]
    ax.barh(s.short, s.pct * 100, color=cols)
    for y, (p, sc) in enumerate(zip(s.pct, s.score)):
        ax.text(p * 100 + 1, y, f"{p*100:.1f}%  ({sc:g}/100)", va="center", fontsize=9)
    ax.axvline(41.875, color=C["ink"], ls="--", lw=1)
    ax.text(42.3, -0.75, "Facility average 41.9%", fontsize=8, color=C["ink"])
    ax.set_xlim(0, 80)
    ax.set_xlabel("% of maximum score achieved")
    ax.set_title("Core component performance (weakest at the top)", loc="left", fontweight="bold")
    fig.tight_layout()
    fig.savefig(FIG / "01_core_component_scores.png")
    plt.close(fig)


def fig_radar(summ):
    labels = list(summ.short)
    vals = list(summ.pct * 100)
    ang = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    vals += vals[:1]
    ang += ang[:1]
    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True))
    ax.plot(ang, vals, color=C["teal"], lw=2)
    ax.fill(ang, vals, color=C["teal"], alpha=.25)
    ax.set_xticks(ang[:-1])
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 100)
    ax.set_yticks([25, 50, 75, 100])
    ax.set_yticklabels(["25%", "50%", "75%", "100%"], fontsize=7)
    ax.set_title("Capability profile across the eight core components", fontweight="bold", pad=20)
    fig.tight_layout()
    fig.savefig(FIG / "02_radar_profile.png")
    plt.close(fig)


def fig_status(df):
    t = df.pivot_table(index="cc", columns="status", values="id", aggfunc="count", fill_value=0)
    t = t[["Fully met", "Partially met", "Not met"]]
    t.index = [CC_SHORT[i] for i in t.index]
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    left = np.zeros(len(t))
    for col, color in zip(t.columns, [C["green"], C["amber"], C["red"]]):
        ax.barh(t.index, t[col], left=left, color=color, label=col)
        for y, (l, v) in enumerate(zip(left, t[col])):
            if v:
                ax.text(l + v / 2, y, int(v), ha="center", va="center", color="white",
                        fontsize=9, fontweight="bold")
        left += t[col].values
    ax.invert_yaxis()
    ax.set_xlabel("Number of indicators")
    ax.legend(ncol=3, loc="lower center", bbox_to_anchor=(.5, 1.0), frameon=False)
    ax.set_title("How many indicators are met, by core component", loc="left", fontweight="bold", pad=28)
    fig.tight_layout()
    fig.savefig(FIG / "03_indicator_status.png")
    plt.close(fig)


def fig_top_gaps(df, min_gap=10):
    import textwrap
    t = df[df.gap >= min_gap].sort_values(["gap", "id"], ascending=[False, True]).iloc[::-1]
    names = [f"{i}  " + textwrap.fill(l, 56) for i, l in zip(t.id, t.label)]
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(t) + 1.4))
    ax.barh(names, t.gap, color=C["red"])
    for y, g in enumerate(t.gap):
        ax.text(g + .15, y, f"{g:g}", va="center", fontsize=8)
    ax.set_xlabel("Points lost (max minus score)")
    ax.set_xlim(0, 17)
    ax.set_title(f"The {len(t)} indicators that each cost 10 or more points", loc="left", fontweight="bold")
    ax.tick_params(axis="y", labelsize=7.5)
    fig.tight_layout()
    fig.savefig(FIG / "04_top_gaps.png")
    plt.close(fig)


def fig_roadmap(road):
    base = 335
    steps = []
    for p, nm in [(1, "Phase 1\ngovernance &\nquick fixes"), (2, "Phase 2\ncapability\nbuilding"),
                  (3, "Phase 3\nstructural\ninvestment")]:
        steps.append((nm, sum(a["points"] for a in road if a["phase"] == p)))
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    ax.bar(0, base, color=C["grey"])
    ax.text(0, base + 8, f"{base}", ha="center", fontweight="bold")
    run = base
    for i, (nm, pts) in enumerate(steps, 1):
        ax.bar(i, pts, bottom=run, color=C["teal"])
        ax.text(i, run + pts + 8, f"+{pts:g}\n= {run+pts:g}", ha="center", fontsize=9, fontweight="bold")
        run += pts
    for thr, nm in [(400.5, "Intermediate (401)"), (600.5, "Advanced (601)")]:
        ax.axhline(thr, color=C["amber"], ls="--", lw=1)
        ax.text(3.45, thr + 6, nm, fontsize=8, ha="right")
    ax.set_xticks(range(4))
    ax.set_xticklabels(["Baseline\n(Oct 2026)"] + [s[0] for s in steps], fontsize=8.5)
    ax.set_ylim(0, 820)
    ax.set_ylabel("IPCAF score (max 800)")
    ax.set_title("Projected score if each phase's actions are fully achieved (upper bound)",
                 loc="left", fontweight="bold", fontsize=10)
    fig.tight_layout()
    fig.savefig(FIG / "05_roadmap_projection.png")
    plt.close(fig)


# ---------------------------------------------------------------- MAIN
def main():
    style()
    df, subtotals, summary = load_items()
    print(f"Loaded {len(df)} indicators from {RAW.name}\n")
    validate(df, subtotals, summary)
    df = enrich(df)

    summ = (df.groupby("cc").agg(score=("score", "sum"), max=("max", "sum"), items=("id", "count"),
                                 fully=("status", lambda s: (s == "Fully met").sum()),
                                 partial=("status", lambda s: (s == "Partially met").sum()),
                                 notmet=("status", lambda s: (s == "Not met").sum()),
                                 lost=("gap", "sum")).reset_index())
    summ["pct"] = summ.score / summ["max"]
    summ["name"] = summ.cc.map(CC_NAMES)
    summ["short"] = summ.cc.map(CC_SHORT)
    total = float(df.score.sum())

    road = build_roadmap(df)
    phase_pts = {p: sum(a["points"] for a in road if a["phase"] == p) for p in (1, 2, 3)}

    theme_rows = []
    for t in THEMES:
        sub = df[df.themes.map(lambda x: t in x)]
        theme_rows.append(dict(theme=t, indicators=len(sub), score=float(sub.score.sum()),
                               max=float(sub["max"].sum()), pct=float(sub.score.sum() / sub["max"].sum()),
                               not_met=int((sub.status == "Not met").sum())))
    themes = pd.DataFrame(theme_rows)

    # ---- console summary used to write the report
    print("\n== Core components ==")
    print(summ[["cc", "short", "score", "pct", "fully", "partial", "notmet", "lost"]].round(3).to_string(index=False))
    print(f"\nTotal {total:g}/800 ({total/8:.2f}%) -> {level_of(total)}; gap to Intermediate: {401-total:g}")
    print("Indicators:", df.status.value_counts().to_dict(), "| points lost:", df.gap.sum())
    print("\n== Themes ==\n", themes.round(3).to_string(index=False))
    print("\n== Roadmap points by phase ==", phase_pts,
          "-> cumulative", {p: total + sum(phase_pts[q] for q in range(1, p + 1)) for p in (1, 2, 3)})
    print("Roadmap total vs total points lost:", sum(phase_pts.values()), "/", df.gap.sum())
    print("\n== Unanswered ==\n", df[df.unanswered][["id", "label", "gap"]].to_string(index=False))
    print("Sensitivity if unanswered all fully met:", total + df[df.unanswered].gap.sum())
    print("\nZero-score share of points lost:",
          round(df[df.status == "Not met"].gap.sum() / df.gap.sum(), 3))
    print("\nTop gaps:\n", df.sort_values(["gap", "id"], ascending=[False, True])
          .head(12)[["id", "label", "gap"]].to_string(index=False))
    cc_lost = summ.set_index("short").lost
    print("\nShare of lost points by CC:\n", (cc_lost / cc_lost.sum()).round(3).to_string())
    print("\nPhase 1 points by effort:", {e: sum(a['points'] for a in road if a['phase'] == 1 and a['effort'] == e)
                                          for e in ("Low", "Medium")})

    # ---- write outputs
    out = df.drop(columns=["themes"]).assign(themes=df.themes.map(" | ".join))
    out.to_csv(PROC / "ipcaf_items.csv", index=False)
    summ.to_csv(PROC / "ipcaf_summary.csv", index=False)
    themes.to_csv(PROC / "ipcaf_themes.csv", index=False)
    pd.DataFrame(road).assign(indicators=lambda d: d.indicators.map(", ".join)).to_csv(
        PROC / "ipcaf_roadmap.csv", index=False)

    payload = dict(
        meta=dict(assessment_date="2026-10-12", source="WHO IPCAF (WHO/HIS/SDS/2018.9)", total=total,
                  max=800, level=level_of(total), levels=[dict(lo=a, hi=b, name=c) for a, b, c in LEVELS]),
        cc=summ.to_dict("records"),
        items=[dict(id=r.id, cc=int(r.cc), no=r.no, question=r.question, label=r.label,
                    answer=None if r.unanswered else r.answer, score=r.score, max=r.max, gap=r.gap,
                    status=r.status, unanswered=bool(r.unanswered), reverse=bool(r.reverse_scored),
                    themes=r.themes) for r in df.itertuples()],
        themes=themes.to_dict("records"), roadmap=road)
    (PROC / "dashboard_data.json").write_text(json.dumps(payload, indent=1, default=float))

    fig_cc(summ)
    fig_radar(summ)
    fig_status(df)
    fig_top_gaps(df)
    fig_roadmap(road)
    print("\nWrote CSVs, JSON and figures.")


if __name__ == "__main__":
    main()
