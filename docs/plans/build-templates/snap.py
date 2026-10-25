"""One LeoCAD snapshot of an MPD file, for the renders in this folder.

The toolkit's `external.render` also writes a LeoCAD BOM, which needs a newer LeoCAD than some machines have (21.06
has no `-csv` switch). Here only the image is made, with the same switches. Without a display it runs under
`xvfb-run`.
"""
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from ldraw_tools.external import cad_source


def available():
    return shutil.which('leocad') is not None and (bool(os.environ.get('DISPLAY')) or shutil.which('xvfb-run') is not None)


def snap(mpd, png, library, *, view='home', timeout=300, size=(1000, 800)):
    """Render `mpd` to `png` (one viewpoint). Raises ValueError when LeoCAD fails."""
    png = Path(png).resolve()
    png.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.snap-', dir=png.parent) as temp:
        source, lib, _ = cad_source(mpd, library, temp)
        target = Path(temp) / f'{view}.png'
        command = ['leocad', '-l', str(lib), '-i', str(target), '-w', str(size[0]), '-h', str(size[1]),
                   '--aa-samples', '4', '--shading', 'full', '--line-width', '1', '--no-highlight', '--no-fade-steps',
                   '--viewpoint', view, str(Path(source).resolve())]
        if not os.environ.get('DISPLAY'):
            command = ['xvfb-run', '-a', '-s', '-screen 0 1280x1024x24', *command]
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        if result.returncode or not target.exists():
            raise ValueError(f'LeoCAD render failed: {result.stdout[-400:]}\n{result.stderr[-400:]}')
        target.replace(png)
    return png
