#!/usr/bin/env python3
"""Reference-guided color adjustment under an explicit mask; RAW to 16-bit TIFF."""
import argparse, io, json
from pathlib import Path
import numpy as np
from PIL import Image, ImageCms, ImageOps

RAW_EXT = {'.dng','.cr2','.cr3','.nef','.nrw','.arw','.srw','.rw2','.raf','.orf','.pef','.3fr','.iiq','.rwl'}

def read_srgb(path):
    path = Path(path)
    if path.suffix.lower() in RAW_EXT:
        try: import rawpy
        except ImportError as exc: raise ValueError('RAW requires rawpy; install requirements.txt') from exc
        try:
            with rawpy.imread(str(path)) as raw:
                a = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=16, output_color=rawpy.ColorSpace.sRGB)
        except Exception as exc: raise ValueError(f'RAW decode failed: {exc}') from exc
        return a.astype(np.float32)/65535, None, 'RAW developed to 16-bit sRGB, camera WB, no automatic brightening', True
    source = ImageOps.exif_transpose(Image.open(path))
    profile = source.info.get('icc_profile'); alpha = source.getchannel('A') if 'A' in source.getbands() else None
    if path.suffix.lower() in {'.tif','.tiff'}:
        try: import tifffile
        except ImportError as exc: raise ValueError('TIFF requires tifffile; install requirements.txt') from exc
        a = tifffile.imread(str(path))
        if a.dtype == np.uint16 and a.ndim == 3 and a.shape[2] == 3:
            with tifffile.TiffFile(str(path)) as tf:
                orientation = tf.pages[0].tags.get('Orientation')
                if orientation and orientation.value != 1: raise ValueError('16-bit TIFF orientation must be normalized before processing')
            if profile:
                try: name=ImageCms.getProfileName(ImageCms.ImageCmsProfile(io.BytesIO(profile))).lower()
                except Exception as exc: raise ValueError(f'Cannot read TIFF profile: {exc}') from exc
                if 'srgb' not in name: raise ValueError('16-bit TIFF must be sRGB; convert its profile before processing')
            return a.astype(np.float32)/65535, None, '16-bit TIFF sRGB' if profile else 'untagged 16-bit TIFF: assumed sRGB', True
    if profile:
        try: source=ImageCms.profileToProfile(source.convert('RGB'),ImageCms.ImageCmsProfile(io.BytesIO(profile)),ImageCms.createProfile('sRGB'),outputMode='RGB')
        except Exception as exc: raise ValueError(f'Cannot convert embedded profile: {exc}') from exc
        note='embedded profile converted to sRGB'
    else:
        source=source.convert('RGB'); note='untagged: assumed sRGB'
    return np.asarray(source,dtype=np.float32)/255, alpha, note, False

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('reference','target','mask','output','reference-box'): p.add_argument('--'+arg,required=True)
    p.add_argument('--strength',type=float,default=.65);args=p.parse_args()
    if not 0<=args.strength<=1: p.error('strength must be between 0 and 1')
    out=Path(args.output)
    if out.exists() or out.resolve() in {Path(args.reference).resolve(),Path(args.target).resolve(),Path(args.mask).resolve()}: p.error('output must be a new file')
    if out.suffix.lower() not in {'.png','.tif','.tiff'}: p.error('output must be PNG or TIFF')
    try:
        ref,_,rn,_=read_srgb(args.reference);target,alpha,tn,high=read_srgb(args.target)
        if high and out.suffix.lower()=='.png': raise ValueError('RAW and 16-bit targets require 16-bit TIFF output')
        mask_image=ImageOps.exif_transpose(Image.open(args.mask))
        if mask_image.size != (target.shape[1],target.shape[0]): raise ValueError('mask dimensions must match the developed target image')
        mask=np.asarray(mask_image.getchannel('A') if 'A' in mask_image.getbands() else mask_image.convert('L'),dtype=np.float32)/255
        if mask.max()==0: raise ValueError('mask is empty')
        box=tuple(map(int,args.reference_box.split(',')))
        if len(box)!=4 or not (0<=box[0]<box[2]<=ref.shape[1] and 0<=box[1]<box[3]<=ref.shape[0]): raise ValueError('reference box must fit the developed reference image')
        ref_mean=ref[box[1]:box[3],box[0]:box[2]].mean(axis=(0,1)); target_mean=np.sum(target*mask[...,None],axis=(0,1))/mask.sum()
        weights=np.array([.2126,.7152,.0722],dtype=np.float32);delta=ref_mean-target_mean;delta-=np.dot(delta,weights)
        adjusted=target+mask[...,None]*args.strength*delta;clipped=int(np.count_nonzero((adjusted<0)|(adjusted>1)))
        profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes();out.parent.mkdir(parents=True,exist_ok=True)
        if out.suffix.lower() in {'.tif','.tiff'}:
            import tifffile
            result=np.clip(np.rint(adjusted*65535),0,65535).astype(np.uint16)
            if alpha is not None: result=np.dstack((result,np.asarray(alpha,dtype=np.uint16)*257))
            tifffile.imwrite(out,result,photometric='rgb',extrasamples=['unassalpha'] if alpha is not None else None,extratags=[(34675,'B',len(profile),profile,False)],metadata=None)
            depth=16
        else:
            result=np.clip(np.rint(adjusted*255),0,255).astype(np.uint8);image=Image.fromarray(result,'RGB')
            if alpha is not None:image.putalpha(alpha)
            image.save(out,icc_profile=profile);depth=8
        print(json.dumps({'output':str(out),'size':[target.shape[1],target.shape[0]],'bit_depth':depth,'reference_box':box,'strength':args.strength,'reference_color_handling':rn,'target_color_handling':tn,'masked_pixels':int(np.count_nonzero(mask)),'clipped_channel_values':clipped},ensure_ascii=False))
    except (OSError,ValueError,ImportError) as exc:p.exit(2,f'Error: {exc}\n')
if __name__=='__main__':main()
