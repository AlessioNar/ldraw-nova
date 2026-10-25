"""State shared by the components while a building is assembled."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import footprint
from .canvas import Canvas
from .colours import colour_code


@dataclass
class Storey:
    index: int
    kind: str                 # 'main' or 'tower'
    courses: int
    y_floor: int              # plate height of the first course's bottom
    cells: set                # footprint including square towers, excluding nothing
    tower_cells: set          # cells that belong to square towers
    blocked: set              # cells taken by round towers
    course0: int              # global course index of this storey's first course
    walls: footprint.Walls = None
    carve: dict = field(default_factory=dict)       # local course -> set of cells left open
    openings: list = field(default_factory=list)
    shutters: dict = field(default_factory=dict)    # local course -> cells coloured as shutters
    keystones: dict = field(default_factory=dict)   # local course -> cells coloured as keystones
    band_h: int = 1           # plates of floor band on top (2 when an interior floor or deck is laid)

    @property
    def y_band(self):
        return self.y_floor + 3 * self.courses

    @property
    def y_top(self):
        return self.y_band + self.band_h


class Ctx:
    def __init__(self, cfg, inventory=None):
        self.cfg = cfg
        self.inv = inventory
        self.canvas = Canvas(inventory=inventory)
        self.notes = []
        self.t = cfg['walls']['thickness']
        self.modular = cfg['base']['kind'] == 'modular'
        self.storeys = []
        self.y_ground = 0
        self.tower_rects = []          # [(i0, j0, w, d, shape)]
        self.facade_slots = {}         # (side, line, a) -> centres used on the ground floor
        self.top_main = 0              # plate height of the main building's top band (top of the last main storey's band)
        self.door = None
        self._colours = {}

    def colour(self, group, key):
        spec = self.cfg[group][key]
        return self.code(spec, glass=key == 'glass_colour')

    def code(self, spec, glass=False):
        code = colour_code(spec, glass=glass)
        if self.inv is not None and self.inv.mode == 'only' and not glass:
            if code not in self._colours:
                if self.inv.has_colour(code):
                    self._colours[code] = code
                else:
                    alt = self.inv.nearest_colour(code)
                    self._colours[code] = alt
                    if alt != code:
                        self.inv.substitutions.append(dict(wanted=code, used=alt, reason='colour not owned'))
            return self._colours[code]
        return code

    def note(self, text):
        if text not in self.notes:
            self.notes.append(text)
