"""Quick probe of the unofficial ONCF GTFS feed: find Casa Voyageurs -> Oujda trips."""
import csv
import sys
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "orhazal"


def read(name):
    with open(DATA / name, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


stops = {s["stop_id"]: s for s in read("stops.txt")}
trips = read("trips.txt")
stop_times = read("stop_times.txt")
routes = {r["route_id"]: r for r in read("routes.txt")}

by_trip = defaultdict(list)
for st in stop_times:
    by_trip[st["trip_id"]].append(st)

for v in by_trip.values():
    v.sort(key=lambda x: int(x["stop_sequence"]))


def fmt_time(t):
    h, m, s = (int(x) for x in t.split(":"))
    return f"{h:02d}:{m:02d}"


hits = []
for t in trips:
    seq = by_trip[t["trip_id"]]
    ids = [s["stop_id"] for s in seq]
    if "200_RAIL" in ids and "490_RAIL" in ids:
        i_o, i_d = ids.index("200_RAIL"), ids.index("490_RAIL")
        if i_o < i_d:
            hits.append((t, seq, i_o, i_d))

print(f"Feed trips: {len(trips)} | stop_times: {len(stop_times)} | stops: {len(stops)}")
print(f"Casa Voyageurs -> Oujda direct trips: {len(hits)}\n")

for t, seq, i_o, i_d in hits:
    dep = seq[i_o]["departure_time"]
    arr = seq[i_d]["arrival_time"]
    route = routes[t["route_id"]]
    print(f"--- train {t['trip_short_name']} ({route['route_short_name']}) {t['trip_id']} dep {fmt_time(dep)} arr {fmt_time(arr)} ---")
    for st in seq[i_o : i_d + 1]:
        s = stops[st["stop_id"]]
        a = fmt_time(st["arrival_time"]) if st["arrival_time"] else "--"
        d = fmt_time(st["departure_time"]) if st["departure_time"] else "--"
        print(f"   {st['stop_sequence']:>3} {s['stop_name']:<28} arr {a}  dep {d}   (id {s['stop_id']})")
    print()
