"""Parser for ONCF's official "Grille Horaire" PDFs.

These PDFs are the operator's published per-line timetables:

    Grille Horaire "Tanger - Oujda" à partir du 14 Septembre 2026
    TRAINS EN PROVENANCE DE TANGER
    Train | Tanger Ville | Dalia | ... | Oujda
    301   | 07:40        | 07:58 | ... | 18:50

Each train row prints **one time per station** (the departure), so the grid is
authoritative for *train identity and departure times*, not for arrival/departure
splits. Its main use here is verification: given a station and a time, which
train number does ONCF print?

The layout is not a clean table: the drawn column borders cut through time
strings, and multi-line station names overlap horizontally. The parser therefore
ignores the borders, clusters the *time tokens* by x position to recover the real
columns, and assigns header words to the nearest column.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pymupdf

TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
TRAIN_RE = re.compile(r"^\*?(\d{1,4})$")
# Some services are lettered: airport shuttles "A5", Marrakech–Tanger "MT".
TRAIN_TOKEN_RE = re.compile(r"^\*?[A-Z0-9]{1,6}$")
TRAIN_STOPWORDS = {
    "TRAIN", "TRAINS", "SENS", "DE", "EN", "N", "N°", "AL", "ATLAS", "PROVENANCE", "GARE",
}
EFFECTIVE_RE = re.compile(r"(\d{1,2})\s+([A-Za-zéûôèÉÛÔÈ]+)\s+(\d{4})")

FRENCH_MONTHS = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12,
}

# How far (in PDF points) a header word may sit from a time column to label it.
HEADER_X_TOLERANCE = 26
# Two clock strings closer than this on the x axis belong to the same station.
COLUMN_X_TOLERANCE = 12
# Words within this vertical distance form one line/row.
LINE_Y_TOLERANCE = 3.5


def normalize_name(value: str) -> str:
    """Accent-insensitive, punctuation-insensitive station key."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("’", "'").replace("-", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def parse_clock(value: str) -> int | None:
    """'07:40' -> 460 minutes, or None."""
    if not value or not TIME_RE.match(value):
        return None
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


@dataclass
class GridTrip:
    train: str
    times: dict[str, str] = field(default_factory=dict)

    def ordered(self) -> list[tuple[str, str]]:
        return list(self.times.items())


@dataclass
class GridSection:
    heading: str
    stations: list[str] = field(default_factory=list)
    trips: list[GridTrip] = field(default_factory=list)


@dataclass
class GridTimetable:
    source: Path
    title: str
    effective: date | None
    sections: list[GridSection] = field(default_factory=list)

    def trains(self) -> list[GridTrip]:
        return [trip for section in self.sections for trip in section.trips]

    def describe(self) -> str:
        when = self.effective.isoformat() if self.effective else "unknown date"
        return f"{self.source.name} · effective {when} · {len(self.trains())} trains"


@dataclass
class _Word:
    x0: float
    x1: float
    y: float
    text: str

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class _Column:
    center: float
    label: str = ""
    words: list[_Word] = field(default_factory=list)


def _extract_words(page: pymupdf.Page) -> list[_Word]:
    return [
        _Word(x0=w[0], x1=w[2], y=(w[1] + w[3]) / 2, text=w[4])
        for w in page.get_text("words")
    ]


def _group_lines(words: list[_Word]) -> list[list[_Word]]:
    lines: list[list[_Word]] = []
    for word in sorted(words, key=lambda w: w.y):
        if lines and abs(lines[-1][0].y - word.y) <= LINE_Y_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda w: w.x0)
    return lines


def _cluster_columns(words: list[_Word]) -> list[_Column]:
    centers = sorted(w.center for w in words if TIME_RE.match(w.text))
    if not centers:
        return []
    groups: list[list[float]] = [[centers[0]]]
    for center in centers[1:]:
        if center - groups[-1][-1] <= COLUMN_X_TOLERANCE:
            groups[-1].append(center)
        else:
            groups.append([center])
    return [_Column(center=sum(group) / len(group)) for group in groups]


def _label_columns(columns: list[_Column], header_words: list[_Word]) -> None:
    for word in header_words:
        if TIME_RE.match(word.text) or TRAIN_RE.match(word.text):
            continue
        if not columns:
            continue
        nearest = min(columns, key=lambda column: abs(column.center - word.center))
        if abs(nearest.center - word.center) <= HEADER_X_TOLERANCE:
            nearest.words.append(word)

    for column in columns:
        ordered = sorted(column.words, key=lambda w: (round(w.y), w.x0))
        # Bilingual sheets print the Arabic and Latin names in the same column.
        # Keep the Latin ones for display (matching strips accents either way).
        latin = [word for word in ordered if not _has_arabic(word.text)]
        if latin:
            ordered = latin
        parts: list[str] = []
        for word in ordered:
            if parts and parts[-1].endswith("-"):
                parts[-1] = parts[-1][:-1] + word.text
            else:
                parts.append(word.text)
        column.label = _clean_label(" ".join(parts))


def _has_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def _clean_label(label: str) -> str:
    label = label.replace("’", "'")
    label = re.sub(r"\s+", " ", label).strip(" .|")
    return label


def _find_column(columns: list[_Column], word: _Word) -> _Column | None:
    if not columns:
        return None
    nearest = min(columns, key=lambda column: abs(column.center - word.center))
    return nearest if abs(nearest.center - word.center) <= COLUMN_X_TOLERANCE else None


def _effective_date(title: str) -> date | None:
    match = EFFECTIVE_RE.search(title)
    if not match:
        return None
    day, month_name, year = match.groups()
    month = FRENCH_MONTHS.get(normalize_name(month_name))
    if not month:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def parse_grid_pdf(path: str | Path) -> GridTimetable:
    path = Path(path)
    document = pymupdf.open(path)
    first_page_words = _extract_words(document[0])
    first_lines = _group_lines(first_page_words)
    title = " ".join(word.text for line in first_lines[:2] for word in line).strip()
    effective = _effective_date(title)

    sections: list[GridSection] = []
    for page in document:
        words = _extract_words(page)
        lines = _group_lines(words)

        anchors = [
            word
            for line in lines
            for word in line
            if word.text.strip().lower() in {"provenance", "sens"}
        ]
        anchors.sort(key=lambda word: (round(word.y, 1), word.x0))

        for anchor in anchors:
            same_line = sorted(
                (other for other in anchors if abs(other.y - anchor.y) <= LINE_Y_TOLERANCE),
                key=lambda word: word.x0,
            )
            if len(same_line) > 1:
                position = same_line.index(anchor)
                x_min = 0 if position == 0 else anchor.x0 - 15
                x_max = same_line[position + 1].x0 - 15 if position + 1 < len(same_line) else page.rect.width
            else:
                x_min, x_max = 0, page.rect.width

            next_y = min(
                (other.y for other in anchors if other.y > anchor.y + LINE_Y_TOLERANCE),
                default=page.rect.height,
            )
            box = [
                word
                for word in words
                if x_min <= word.center <= x_max and anchor.y + 4 <= word.y < next_y - 1
            ]
            if len(box) < 4:
                continue

            heading_words = [
                word
                for word in same_line
                if x_min <= word.center <= x_max
            ]
            heading = " ".join(word.text for word in heading_words).strip()
            section = _parse_box(box, heading)
            if section is not None:
                sections.append(section)

    return GridTimetable(source=path, title=title, effective=effective, sections=sections)


def _parse_box(words: list[_Word], heading: str) -> GridSection | None:
    """Parse one boxed area (one direction of one line sheet)."""
    lines = _group_lines(words)
    if not lines:
        return None

    def train_token(line: list[_Word]) -> tuple[str, float] | None:
        times = [word for word in line if TIME_RE.match(word.text)]
        if len(times) < 2:
            return None
        first_time_x = min(word.x0 for word in times)
        for word in line:
            if word.x0 >= first_time_x:
                break
            text = word.text.strip()
            if (
                TRAIN_TOKEN_RE.match(text)
                and text.upper() not in TRAIN_STOPWORDS
                and not TIME_RE.match(text)
            ):
                return text.lstrip("*"), first_time_x
        return None

    # Al Boraq prints separate Départ/Arrivée times per station. That layout
    # needs a different model, so skip it rather than mis-assign times.
    header_probe = " ".join(word.text for line in lines[:4] for word in line).casefold()
    if "départ" in header_probe and "arrivée" in header_probe:
        return None

    data_start = None
    for index, line in enumerate(lines):
        if train_token(line) is not None:
            data_start = index
            break
    if data_start is None:
        return None

    header_words = [word for line in lines[:data_start] for word in line]
    data_words = [word for line in lines[data_start:] for word in line]
    columns = _cluster_columns(data_words)
    _label_columns(columns, header_words)
    if not columns:
        return None

    labelled = [column for column in columns if column.label]
    if not labelled:
        return None

    section = GridSection(heading=heading, stations=[column.label for column in labelled])

    seen: set[str] = set()
    for line in lines[data_start:]:
        token = train_token(line)
        if token is None:
            continue
        train = token[0]
        if train in seen:
            continue
        seen.add(train)
        trip = GridTrip(train=train)
        for word in line:
            if not TIME_RE.match(word.text):
                continue
            column = _find_column(columns, word)
            if column is None or not column.label:
                continue
            trip.times.setdefault(column.label, word.text)
        if trip.times:
            section.trips.append(trip)

    return section if section.trips else None


def _tokens(value: str) -> list[str]:
    return normalize_name(value).split()


def station_matches(label: str, target: str) -> bool:
    """True when a grid label and a query name refer to the same station.

    Handles ``Tanger`` vs ``Tanger Ville``, ``Salé`` vs ``Salé Ville`` and grid
    cells that merged two staggered headers (``Al Akba L'hamra``).
    """
    label_tokens, target_tokens = _tokens(label), _tokens(target)
    if not label_tokens or not target_tokens:
        return False
    if label_tokens == target_tokens:
        return True

    def contains(haystack: list[str], needle: list[str]) -> bool:
        size = len(needle)
        return any(haystack[i : i + size] == needle for i in range(len(haystack) - size + 1))

    return contains(label_tokens, target_tokens) or contains(target_tokens, label_tokens)


def find_train(
    grid: GridTimetable,
    station: str,
    time_text: str,
    *,
    tolerance_minutes: int = 2,
) -> list[tuple[str, str, str]]:
    """Return ``(train, station_label, printed_time)`` matching station + time."""
    target_time = parse_clock(time_text)
    if target_time is None:
        return []
    matches: list[tuple[str, str, str]] = []
    for section in grid.sections:
        for trip in section.trips:
            for label, printed in trip.times.items():
                if not station_matches(label, station):
                    continue
                printed_minutes = parse_clock(printed)
                if printed_minutes is None:
                    continue
                delta = abs(printed_minutes - target_time)
                delta = min(delta, 24 * 60 - delta)
                if delta <= tolerance_minutes:
                    matches.append((trip.train, label, printed))
    return matches


def find_trip(grid: GridTimetable, train: str) -> GridTrip | None:
    for trip in grid.trains():
        if trip.train == train:
            return trip
    return None


@dataclass
class GridMatch:
    train: str
    station: str
    printed: str
    status: str  # "match" | "mismatch" | "no_coverage"
    source: str
    effective: str | None
    sheet: str


class GridLibrary:
    """All downloaded official line grids, queried together."""

    def __init__(self, grids: list[GridTimetable]):
        self.grids = grids

    @classmethod
    def load_dir(cls, directory: str | Path) -> "GridLibrary":
        directory = Path(directory)
        grids: list[GridTimetable] = []
        if not directory.exists():
            return cls(grids)
        for path in sorted(directory.glob("*.pdf")):
            try:
                grid = parse_grid_pdf(path)
            except Exception:
                continue
            if grid.sections:
                grids.append(grid)
        return cls(grids)

    def __len__(self) -> int:
        return len(self.grids)

    def describe(self) -> list[dict]:
        return [
            {
                "file": grid.source.name,
                "title": grid.title,
                "effective": grid.effective.isoformat() if grid.effective else None,
                "trains": [trip.train for trip in grid.trains()],
                "sections": len(grid.sections),
            }
            for grid in self.grids
        ]

    def identify(self, station: str, time_text: str, *, tolerance_minutes: int = 2) -> list[GridMatch]:
        matches: dict[tuple[str, str], GridMatch] = {}
        for grid in self.grids:
            for train, label, printed in find_train(grid, station, time_text, tolerance_minutes=tolerance_minutes):
                key = (train, printed)
                matches.setdefault(
                    key,
                    GridMatch(
                        train=train,
                        station=label,
                        printed=printed,
                        status="match",
                        source=grid.source.name,
                        effective=grid.effective.isoformat() if grid.effective else None,
                        sheet=grid.title,
                    ),
                )
        return sorted(matches.values(), key=lambda m: m.train)

    def verify(
        self, train_number: str, station: str, time_text: str, *, tolerance_minutes: int = 3
    ) -> GridMatch:
        """Check a train's printed time at a station against the official grids."""
        target = parse_clock(time_text)
        best_mismatch: GridMatch | None = None
        covered = False

        for grid in self.grids:
            for section in grid.sections:
                for trip in section.trips:
                    if trip.train != train_number:
                        continue
                    for label, printed in trip.times.items():
                        if not station_matches(label, station):
                            continue
                        covered = True
                        printed_minutes = parse_clock(printed)
                        if target is not None and printed_minutes is not None:
                            delta = abs(printed_minutes - target)
                            delta = min(delta, 24 * 60 - delta)
                            if delta <= tolerance_minutes:
                                return GridMatch(
                                    train=train_number,
                                    station=label,
                                    printed=printed,
                                    status="match",
                                    source=grid.source.name,
                                    effective=grid.effective.isoformat() if grid.effective else None,
                                    sheet=grid.title,
                                )
                        best_mismatch = GridMatch(
                            train=train_number,
                            station=label,
                            printed=printed,
                            status="mismatch",
                            source=grid.source.name,
                            effective=grid.effective.isoformat() if grid.effective else None,
                            sheet=grid.title,
                        )

        if best_mismatch is not None:
            return best_mismatch
        return GridMatch(
            train=train_number,
            station=station,
            printed="",
            status="no_coverage" if not covered else "mismatch",
            source="",
            effective=None,
            sheet="",
        )
