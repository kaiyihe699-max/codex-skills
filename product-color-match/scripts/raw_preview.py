#!/usr/bin/env python3
"""Generate a small preview and dimensions from a RAW for reference/mask planning."""
import argparse, json
from pathlib import Path
import numpy as np
import rawpy
from PIL import Image
p=argparse.ArgumentParser(description=__doc__);p.add_argument('raw');p.add_argument('--output',required=True);p.add_argument('--max-size',type=int,default=1200);a=p.parse_args()
out=Path(a.output)
if out.exists(): p.error('output must be a new file')
if a.max_size<1 or a.max_size>1200:p.error('max-size must be 1..1200')
try:
 with rawpy.imread(a.raw) as raw: x=raw.postprocess(use_camera_wb=True,no_auto_bright=True,output_bps=16,output_color=rawpy.ColorSpace.sRGB)
 image=Image.fromarray(np.rint(x/257).astype(np.uint8),'RGB');original=image.size;image.thumbnail((a.max_size,a.max_size));out.parent.mkdir(parents=True,exist_ok=True);image.save(out)
 print(json.dumps({'developed_size':original,'preview_size':image.size,'output':str(out),'white_balance':'camera','auto_bright':False}))
except Exception as e:p.exit(2,f'RAW preview failed: {e}\n')
