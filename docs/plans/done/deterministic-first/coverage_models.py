"""Throwaway: how many of the annotated reference models match each coarse subject (description text)."""
import re, sqlite3, sys
c = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else 'data/ldraw-info.db')
rows = c.execute("select * from MODELS_DESCRIPTIONS").fetchall()
print([d[1] for d in c.execute('pragma table_info(MODELS_DESCRIPTIONS)')], len(rows))
d = [(r[0], ' '.join(map(str, r[1:]))) for r in rows]
cats = {
    'car': r'\b(car|sedan|coupe|roadster|race car|sports car|hatchback)\b',
    'truck/van/bus': r'\b(truck|lorry|van|bus|pickup|tractor|tanker)\b',
    'boat/ship': r'\b(boat|ship|yacht|ferry|submarine)\b',
    'aircraft': r'\b(plane|aircraft|airplane|jet|helicopter|airliner)\b',
    'spaceship': r'\b(spaceship|spacecraft|starfighter|space ship|shuttle|rocket|starship|fighter)\b',
    'house/building': r'\b(house|cottage|building|shop|store|castle|tower|station|church|barn|hotel|cafe|lodge)\b',
    'train': r'\b(train|locomotive|railway|tram)\b',
    'motorcycle': r'\b(motorcycle|motorbike|bike)\b',
    'robot/figure/animal': r'\b(robot|mech|dragon|animal|dog|cat|creature)\b',
    'technic mech': r'\b(gearbox|suspension|differential|engine|crane|excavator|steering)\b',
}
for k, p in cats.items():
    print(k, sum(1 for a, t in d if re.search(p, t, re.I)))
print(rows[0][:2])
