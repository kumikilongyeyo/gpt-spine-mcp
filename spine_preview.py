"""spine_preview — lightweight blend-aware renderer for rig QA.

Understands slot normal/additive/multiply/screen blending, RGBA timelines,
bone transforms and attachment swaps for automated visual critique.
"""
from __future__ import annotations
import json, os
from PIL import Image, ImageDraw


def _bone_world(bones, name):
    X = Y = 0.0; n = name
    while n and n != "root":
        b = bones[n]; X += b.get("x", 0); Y += b.get("y", 0); n = b.get("parent")
    return X, Y


def _lerp(kf, t):
    if not kf: return {}
    if t <= kf[0].get("time", 0): return kf[0]
    if t >= kf[-1].get("time", 0): return kf[-1]
    for i in range(len(kf)-1):
        a,b=kf[i],kf[i+1]; at,bt=a.get("time",0),b.get("time",0)
        if at <= t <= bt:
            f=(t-at)/max(1e-9,bt-at)
            return {k:(a.get(k,0)+(b.get(k,0)-a.get(k,0))*f if isinstance(a.get(k,0),(int,float)) and isinstance(b.get(k,0),(int,float)) else a.get(k)) for k in (set(a)|set(b))-{"curve"}}
    return kf[-1]


def _hex_rgba(value):
    value=(value or "ffffffff").strip().lstrip("#")
    if len(value)==6: value += "ff"
    try: return tuple(int(value[i:i+2],16) for i in range(0,8,2)) if len(value)==8 else (255,255,255,255)
    except ValueError: return (255,255,255,255)


def _slot_rgba(animation, slot, t, setup="ffffffff"):
    track=animation.get("slots",{}).get(slot,{}).get("rgba")
    if not track: return _hex_rgba(setup)
    if t <= track[0].get("time",0): return _hex_rgba(track[0].get("color"))
    if t >= track[-1].get("time",0): return _hex_rgba(track[-1].get("color"))
    for i in range(len(track)-1):
        a,b=track[i],track[i+1]; at,bt=float(a.get("time",0)),float(b.get("time",0))
        if at <= t <= bt:
            f=(t-at)/max(1e-9,bt-at); ca,cb=_hex_rgba(a.get("color")),_hex_rgba(b.get("color"))
            return tuple(round(ca[c]+(cb[c]-ca[c])*f) for c in range(4))
    return _hex_rgba(setup)


def _tint(im, rgba):
    if rgba==(255,255,255,255): return im
    out=im.convert("RGBA"); p=out.load(); mr,mg,mb,ma=rgba
    for y in range(out.height):
        for x in range(out.width):
            r,g,b,a=p[x,y]; p[x,y]=(r*mr//255,g*mg//255,b*mb//255,a*ma//255)
    return out


def _blend_pixel(dst, src, mode):
    dr,dg,db,da=dst; sr,sg,sb,sa=src; af=sa/255.0; daf=da/255.0
    if mode=="additive":
        br,bg,bb=min(255,round(dr+sr*af)),min(255,round(dg+sg*af)),min(255,round(db+sb*af))
    elif mode=="screen":
        rr=255-(255-dr)*(255-sr)/255; gg=255-(255-dg)*(255-sg)/255; zz=255-(255-db)*(255-sb)/255
        br,bg,bb=round(dr+(rr-dr)*af),round(dg+(gg-dg)*af),round(db+(zz-db)*af)
    elif mode=="multiply":
        rr=dr*sr/255; gg=dg*sg/255; zz=db*sb/255
        br,bg,bb=round(dr+(rr-dr)*af),round(dg+(gg-dg)*af),round(db+(zz-db)*af)
    else:
        oa=af+daf*(1-af)
        if oa<=1e-9: return (0,0,0,0)
        return (round((sr*af+dr*daf*(1-af))/oa),round((sg*af+dg*daf*(1-af))/oa),round((sb*af+db*daf*(1-af))/oa),round(oa*255))
    return (br,bg,bb,round(min(1.0,af+daf*(1-af))*255))


def _composite(canvas, source, xy, mode="normal"):
    if mode=="normal": canvas.alpha_composite(source,xy); return
    x0,y0=xy; src=source.convert("RGBA"); sp=src.load(); cp=canvas.load()
    for sy in range(src.height):
        dy=y0+sy
        if not 0<=dy<canvas.height: continue
        for sx in range(src.width):
            dx=x0+sx
            if not 0<=dx<canvas.width: continue
            pix=sp[sx,sy]
            if pix[3]: cp[dx,dy]=_blend_pixel(cp[dx,dy],pix,mode)


def render_frame(d, images_dir, anim, t, maxpx=0, transparent=False):
    sk=d["skeleton"]; bones={b["name"]:b for b in d["bones"]}; slots=d["slots"]; att=d["skins"][0]["attachments"]; A=d["animations"][anim]
    W,H=int(sk["width"]),int(sk["height"]); cv=Image.new("RGBA",(W,H),(0,0,0,0) if transparent else (24,20,32,255))
    cur={s["name"]:s.get("attachment") for s in slots}
    for sn,ad in A.get("slots",{}).items():
        if ad.get("attachment"):
            name=ad["attachment"][0].get("name")
            for k in ad["attachment"]:
                if k.get("time",0)<=t: name=k.get("name")
            cur[sn]=name
    bd={}
    for bn,tl in A.get("bones",{}).items():
        e={"rot":0,"tx":0,"ty":0,"sx":1,"sy":1}
        if "rotate" in tl: e["rot"]=_lerp(tl["rotate"],t).get("value",0)
        if "translate" in tl:
            v=_lerp(tl["translate"],t); e["tx"],e["ty"]=v.get("x",0),v.get("y",0)
        if "scale" in tl:
            v=_lerp(tl["scale"],t); e["sx"],e["sy"]=v.get("x",1),v.get("y",1)
        bd[bn]=e
    for s in slots:
        region=cur.get(s["name"])
        if not region: continue
        ent=att.get(s["name"],{}).get(region)
        if not ent: continue
        path=ent.get("path",region); p=os.path.join(images_dir,f"{path}.png")
        if not os.path.exists(p): continue
        im=Image.open(p).convert("RGBA"); target=(max(1,int(ent.get("width",im.width))),max(1,int(ent.get("height",im.height))))
        if im.size!=target: im=im.resize(target,Image.LANCZOS)
        bx,by=_bone_world(bones,s["bone"]); e=bd.get(s["bone"],{"rot":0,"tx":0,"ty":0,"sx":1,"sy":1})
        if e["sx"]!=1 or e["sy"]!=1:
            im=im.resize((max(1,int(im.width*abs(e["sx"]))),max(1,int(im.height*abs(e["sy"]))),),Image.LANCZOS)
            if e["sx"]<0: im=im.transpose(Image.FLIP_LEFT_RIGHT)
            if e["sy"]<0: im=im.transpose(Image.FLIP_TOP_BOTTOM)
        if e["rot"]: im=im.rotate(e["rot"],expand=True,resample=Image.BICUBIC)
        im=_tint(im,_slot_rgba(A,s["name"],t,s.get("color","ffffffff")))
        px=bx+ent.get("x",0)+e["tx"]; py=by+ent.get("y",0)+e["ty"]; xy=(int(px+W/2-im.width/2),int(H-py-im.height/2))
        _composite(cv,im,xy,s.get("blend","normal"))
    if maxpx:
        sc=maxpx/max(W,H); cv=cv.resize((max(1,int(W*sc)),max(1,int(H*sc))),Image.LANCZOS)
    return cv


_render=lambda d,images_dir,anim,t,maxpx: render_frame(d,images_dir,anim,t,maxpx,False)
_POSES={"idle":[0.0,1.4],"win":[0.1,0.28,0.5],"blink":[0.15],"pop":[0.0]}


def montage(rig_json,images_dir,out_png,maxpx=200):
    d=json.load(open(rig_json,encoding="utf-8")); shots=[(a,t) for a in d["animations"] for t in _POSES.get(a,[0.0])]
    cells=[(render_frame(d,images_dir,a,t,maxpx),f"{a}@{t}") for a,t in shots]
    if not cells: raise ValueError("no animations to preview")
    cw,ch=cells[0][0].size; mont=Image.new("RGBA",(len(cells)*cw+8*(len(cells)+1),ch+22),(0,0,0,255)); dr=ImageDraw.Draw(mont)
    for i,(im,lbl) in enumerate(cells):
        x=8+i*(cw+8); mont.paste(im,(x+(cw-im.width)//2,10+(ch-im.height)//2),im); dr.text((x+2,ch+8),lbl,fill=(255,255,255,255))
    os.makedirs(os.path.dirname(out_png) or ".",exist_ok=True); mont.convert("RGB").save(out_png); return out_png
