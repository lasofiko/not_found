"""График ценовой надбавки: три панели с собственной шкалой. Запуск: python price_premium_chart.py results"""
import csv, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

out = Path(sys.argv[1])
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
PANELS = [("kaliningrad_39", "Калининградская область", "#2a78d6"),
          ("kamchatka_41", "Камчатский край", "#eb6834"),
          ("primorye_25", "Приморский край", "#1baf7a")]
x = lambda p: int(p[:4]) + (int(p[5:]) - 1) / 12
read = lambda name: list(csv.DictReader((out / name).open(encoding="utf-8-sig")))
history, forecast = read("history.csv"), read("forecast.csv")

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": GRID, "text.color": INK})
fig, axes = plt.subplots(1, 3, figsize=(11, 3.6), facecolor=SURFACE)
for ax, (region, title, color) in zip(axes, PANELS):
    h = [(x(r["period"]), float(r["value_pp"])) for r in history if r["region"] == region]
    f = [(x(r["period"]), float(r["value_pp"]), float(r["lower_80"]), float(r["upper_80"])) for r in forecast if r["region"] == region]
    ax.set_facecolor(SURFACE)
    ax.axvline(2022 + 2 / 12, color=GRID, lw=1)
    ax.fill_between([p[0] for p in f], [p[2] for p in f], [p[3] for p in f], color=color, alpha=0.18, lw=0)
    ax.plot([p[0] for p in h], [p[1] for p in h], color=color, lw=2, solid_capstyle="round")
    ax.plot([h[-1][0]] + [p[0] for p in f], [h[-1][1]] + [p[1] for p in f], color=color, lw=2, ls=(0, (2, 2)))
    ax.plot(*h[-1], "o", ms=6, color=color, mec=SURFACE, mew=2)
    ax.annotate(f"{h[-1][1]:.1f}".replace(".", ","), h[-1], xytext=((-16, 9) if region == "kamchatka_41" else (0, -16)), textcoords="offset points", ha="center", color=INK, fontsize=9)
    low, high = min(p[1] for p in h + [(0, q[2]) for q in f]), max(p[1] for p in h + [(0, q[3]) for q in f])
    pad = (high - low) * 0.12
    ax.set_ylim(low - pad, high + pad * 1.6)
    ax.text(2022 + 2 / 12, low - pad * 0.6, " март 2022", color=MUTED, fontsize=8, va="bottom")
    ax.set_title(title, loc="left", fontsize=10, color=INK, pad=8)
    ax.set_xlim(2015, 2028.2)
    ax.set_xticks([2015, 2018, 2021, 2024, 2027])
    ax.tick_params(colors=MUTED, length=0)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
axes[0].set_ylabel("надбавка к России, п. п.", color=MUTED)
fig.suptitle("Ценовая надбавка: стоимость фиксированного набора в регионе относительно России", x=0.012, ha="left", fontsize=11, color=INK)
fig.text(0.012, 0.01, "Сплошная линия — расчёт по данным Росстата (ЕМИСС 31052), январь 2015 — сентябрь 2026; подпись — значение за сентябрь 2026.\n"
         "Пунктир и полоса — прогноз до декабря 2027 и интервал 80%. У каждой панели своя шкала.", color=MUTED, fontsize=8, va="bottom")
fig.tight_layout(rect=(0, 0.09, 1, 0.94))
fig.savefig(out / "price-premium.png", dpi=180, facecolor=SURFACE)
