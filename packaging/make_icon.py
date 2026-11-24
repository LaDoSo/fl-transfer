"""Draw packaging/icon.ico: a pattern clip, the way the cards in the window look.  Needs Pillow."""
import os
from PIL import Image, ImageDraw

S = 1024                                            # draw large, scale down
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((40, 40, S - 40, S - 40), radius=190, fill=(27, 33, 38, 255))
d.rounded_rectangle((40, 40, S - 40, 330), radius=190, fill=(240, 160, 60, 255))
d.rectangle((40, 230, S - 40, 330), fill=(240, 160, 60, 255))
for x, y, w in ((150, 430, 250), (430, 520, 180), (640, 430, 230), (150, 610, 160), (340, 700, 300),
                (680, 610, 190), (150, 790, 220), (560, 790, 310)):
    d.rounded_rectangle((x, y, x + w, y + 52), radius=18, fill=(214, 222, 226, 255))
for k in range(3):                                  # the grip lines of a clip header
    d.rounded_rectangle((150, 130 + k * 44, 300, 152 + k * 44), radius=10, fill=(27, 20, 8, 255))
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
im.resize((256, 256), Image.LANCZOS).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
im.resize((256, 256), Image.LANCZOS).save(os.path.splitext(out)[0] + ".png")

# The picture at the left of the installer's first and last page (NSIS wants a 164 x 314 bmp).
W, H = 164 * 4, 314 * 4
side = Image.new("RGB", (W, H), (27, 33, 38))
sd = ImageDraw.Draw(side)
for y in range(H):                                  # a little lighter towards the top
    k = 1 - y / H
    sd.line((0, y, W, y), fill=(int(27 + 14 * k), int(33 + 16 * k), int(38 + 18 * k)))
mark = im.resize((360, 360), Image.LANCZOS)
side.paste(mark, ((W - 360) // 2, 150), mark)
rows = ((60, 700, 250), (350, 700, 180), (150, 790, 330), (60, 880, 160), (270, 880, 300),
        (110, 970, 220), (380, 970, 200), (60, 1060, 380), (200, 1150, 260))
for x, y, w in rows:
    sd.rounded_rectangle((x, y, x + w, y + 34), radius=12, fill=(70, 82, 90))
sd.rectangle((0, H - 28, W, H), fill=(240, 160, 60))
side.resize((164, 314), Image.LANCZOS).save(os.path.join(os.path.dirname(out), "welcome.bmp"))
print("wrote", out)
