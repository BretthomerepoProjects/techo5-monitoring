"""Generate a generic network icon using pixels only; no external images or metadata."""
from pathlib import Path
import math
import struct
import zlib


def make_icon():
    size = 256
    points = ((128, 68), (76, 166), (180, 166))
    def near_line(x, y, a, b):
        dx, dy = b[0]-a[0], b[1]-a[1]
        t = max(0, min(1, ((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy)))
        return math.hypot(x-a[0]-t*dx, y-a[1]-t*dy) <= 4
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            color = (0, 0, 0, 0)
            if (x-128)**2+(y-128)**2 <= 116**2:
                color = (24, 111, 170, 255)
                if any((x-a)**2+(y-b)**2 <= 18**2 for a, b in points) or any(near_line(x, y, points[a], points[b]) for a, b in ((0,1),(0,2),(1,2))):
                    color = (255, 255, 255, 255)
            rows.extend(color)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size,size,8,6,0,0,0))
            + chunk(b'IDAT', zlib.compress(bytes(rows), 9)) + chunk(b'IEND', b''))


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    path = root / 'custom_components/techo5_satellite_manager/brand/icon.png'
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(make_icon())
    print('Generated metadata-free brand icon')
