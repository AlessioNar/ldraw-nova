"""Contact sheets of the preset renders (needs Pillow, which is not a project dependency: run it with system python)."""
from pathlib import Path
from PIL import Image, ImageDraw

HERE = Path(__file__).parent
GROUPS = {'asian': ['pagoda', 'white-pagoda', 'temple-hall', 'minka', 'gate-tower', 'tea-room', 'siheyuan'],
          'general': ['barn', 'town-hall', 'apartment-block', 'courtyard-fort', 'lighthouse', 'warehouse', 'manor'],
          'modular': ['modular-bakery', 'modular-town-hall', 'modular-flats', 'modular-tea-house'],
          'classic': ['house', 'cottage', 'shop', 'townhouse', 'tower', 'castle-keep']}
TITLES = {'pagoda': 'Pagoda (4 tiers)', 'white-pagoda': 'Japanese pagoda (3 tiers)', 'temple-hall': 'Temple hall',
          'minka': 'Japanese farmhouse (minka)', 'gate-tower': 'City gate tower', 'tea-room': 'Tea room',
          'siheyuan': 'Courtyard house (siheyuan)', 'barn': 'Barn', 'town-hall': 'Town hall',
          'apartment-block': 'Apartment block', 'courtyard-fort': 'Courtyard fort', 'lighthouse': 'Lighthouse',
          'warehouse': 'Warehouse', 'manor': 'Manor house', 'house': 'House', 'cottage': 'Cottage', 'shop': 'Shop, flat roof',
          'townhouse': 'Townhouse', 'tower': 'Tower', 'castle-keep': 'Castle keep', 'modular-bakery': 'Modular bakery', 'modular-town-hall': 'Modular town hall',
          'modular-flats': 'Modular apartment block', 'modular-tea-house': 'Modular tea house'}

for group, names in GROUPS.items():
    cols, w, h = (2, 700, 640) if group == 'modular' else (3, 520, 440)
    rows = (len(names) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * w, rows * h), 'white')
    draw = ImageDraw.Draw(sheet)
    for k, name in enumerate(names):
        im = Image.open(HERE / 'renders' / f'{name}-34.png').convert('RGBA')
        bg = Image.new('RGBA', im.size, 'white')
        im = Image.alpha_composite(bg, im).convert('RGB')
        box = Image.eval(im.convert('L'), lambda v: 255 if v < 245 else 0).getbbox()
        if box:
            im = im.crop((max(box[0] - 20, 0), max(box[1] - 20, 0), min(box[2] + 20, im.width), min(box[3] + 20, im.height)))
        im.thumbnail((w - 20, h - 50))
        x, y = (k % cols) * w, (k // cols) * h
        sheet.paste(im, (x + (w - im.width) // 2, y + 30 + (h - 50 - im.height) // 2))
        draw.text((x + 12, y + 8), TITLES[name], fill='black')
    sheet.save(HERE / 'renders' / f'gallery-{group}.png')
